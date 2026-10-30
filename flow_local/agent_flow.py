"""Bounded agent-composer adapter for gflow-cli 0.92's unported frame workflow.

Uses the existing local Google profile. Each paid approval must quote one clip
and no more than the explicit per-clip limit. No private endpoint replay.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def quoted_cost(title: str, ceiling: int) -> int:
    numbers = [int(n) for n in re.findall(r"\d+", title)]
    if len(numbers) < 2 or numbers[0] != 1 or numbers[-1] > ceiling:
        raise ValueError(f"Refusing unclear or excessive Flow quote: {title[:250]}")
    return numbers[-1]


async def generate(request, audit, result_file):
    from .core import DATA, gflow_env
    os.environ.update(gflow_env())
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.video import Aspect, VideoModel
    from gflow_cli.api.transports import agent_only_composer as agent
    from gflow_cli.api.transports.migrated_composer import (
        MigratedComposer, ASPECT_LIGATURE, VIDEO_MODEL_MENU_MATCHERS,
    )

    class BoundedComposer(agent.AgentOnlyComposer):
        async def generate(self,page,*args,**kwargs):
            initial = await page.evaluate(agent._VIDEO_JS)
            self.initial_finished = len(initial['finished'])
            return await super().generate(page,*args,**kwargs)

        async def _ready_video(self,page,state,named,baseline_head):
            ready = await super()._ready_video(page,state,named,baseline_head)
            if ready is not None:
                return ready
            # Flow now serves opaque /asb/ URLs in both tiles and agent replies.
            # A single new playable tile in this exclusively owned run is sufficient;
            # do not fabricate a Google media UUID from the opaque URL.
            if not state['pending'] and len(state['finished']) == self.initial_finished + 1:
                head = state['finished'][0]
                if agent._tile_key(head) != baseline_head:
                    src = head['video'] or await self._hover_video_src(page)
                    if src:
                        audit['opaque_media'] = True
                        return agent.TileMedia('opaque-'+hashlib.sha256(src.encode()).hexdigest()[:20],src)
            return None
        async def _approve_gate(self, page, baseline_gates):
            title = await page.locator(agent.LIVE_GATE).nth(baseline_gates).locator(agent.GATE_TITLE).first.inner_text(timeout=3000)
            try:
                cost = quoted_cost(title, request['max_credits'])
                if request['resolution'] == '720p' and cost != 15:
                    raise ValueError(f"720p 10-second clip must quote 15 credits; Flow quoted {cost}.")
            except ValueError:
                await self._reject_gate(page, baseline_gates)
                raise
            if audit.get('approved_credits') is not None:
                raise ValueError('A second paid approval is not authorized for this clip.')
            audit.update(quote=title, approved_credits=cost, phase='approving')
            result_file.write_text(json.dumps(audit, indent=2))
            await page.screenshot(path=str(result_file.with_suffix('.quote.png')))
            await super()._approve_gate(page, baseline_gates)
            audit['phase'] = 'generating'
            result_file.write_text(json.dumps(audit, indent=2))

        async def _submit(self, page, directive):
            clear = page.get_by_role('button', name='Clear prompt', exact=True)
            if await clear.is_visible():
                await clear.click()
            editor = page.locator(agent.EDITOR).last
            await editor.click()
            await page.keyboard.press('ControlOrMeta+a')
            await page.keyboard.press('Delete')
            await page.keyboard.insert_text(directive)
            frame = request.get('initial_frame')
            if frame:
                before_images = await page.locator('flow-image-tile img').evaluate_all("els => els.map(i=>i.dataset.mediaId || i.src)")
                # These labels are the Flow agent's own media attachment controls.
                attach = page.get_by_role('button', name='Add ingredients to the prompt box', exact=True)
                await attach.click(timeout=8000)
                upload = page.get_by_text(re.compile(r'^Upload media$', re.I)).last
                async with page.expect_file_chooser(timeout=15000) as chooser:
                    await upload.click(timeout=10000)
                    # Flow asks for upload rights once per profile. This frame
                    # comes from this account's own preceding generated clip.
                    agree = page.get_by_role('button', name='I agree', exact=True)
                    with contextlib.suppress(Exception):
                        await agree.wait_for(state='visible', timeout=2000)
                        await agree.click()
                await (await chooser.value).set_files(frame)
                agree = page.get_by_role('button', name='I agree', exact=True)
                with contextlib.suppress(Exception):
                    await agree.wait_for(state='visible', timeout=3000)
                    await agree.click()
                # Uploading adds an asset to the grid; it does not necessarily
                # select it. Wait for this exact new image, then open its detail.
                uploaded = await page.wait_for_function("""before => {
                  const i=[...document.querySelectorAll('flow-image-tile:not(:has(flow-pending-tile)) img')]
                    .find(i=>!before.includes(i.dataset.mediaId || i.src));
                  return i && {id:i.dataset.mediaId,src:i.src};
                }""", arg=before_images, timeout=90000)
                uploaded = await uploaded.json_value()
                # The asset picker overlays the project grid. Select its newest
                # matching filename, rather than clicking through its backdrop.
                await page.get_by_text(Path(frame).name, exact=True).filter(visible=True).first.click(timeout=10000)
                confirm = page.get_by_role('button', name=re.compile(r'^Add to prompt$', re.I)).filter(visible=True).first
                await confirm.wait_for(state='visible', timeout=90000)
                await confirm.click()
                # Wait for the upload's attachment, not merely the file picker to close.
                ingredient = page.get_by_role('button', name='Ingredient', exact=True)
                await ingredient.wait_for(state='visible', timeout=20000)
                if 'videocam' in await ingredient.inner_text():
                    raise ValueError('Reference attachment is a video; expected the uploaded ending image.')
                audit['attachment_media_id'] = uploaded.get('id')
                audit['initial_frame_attached'] = True
            await page.screenshot(path=str(result_file.with_suffix('.prepared.png')))
            audit['phase'] = 'submitting'
            result_file.write_text(json.dumps(audit, indent=2))
            await page.locator(agent.SUBMIT).last.click(timeout=10000)
            audit['submitted'] = True
            result_file.write_text(json.dumps(audit, indent=2))

    async with FlowApiClient(DATA / 'flow-profiles' / ('profile_' + request['profile']), headless=False, transport='ui_automation') as client:
        page = client._page
        await MigratedComposer().ensure_editor(page, request['project_id'])
        composer = BoundedComposer()
        if request.get('recover_only'):
            state = await page.evaluate(agent._VIDEO_JS)
            if state['pending'] or len(state['finished']) != request.get('expected_finished',1):
                raise ValueError('Expected completed tile could not be uniquely identified; no generation attempted.')
            src = await composer._hover_video_src(page)
            if not src:
                raise ValueError('Existing clip has no playable source yet; no generation attempted.')
            async def recover_save(media_id,content):
                output = Path(request['output']);output.write_bytes(content);return output
            path = await agent.download_video(page,src,'recovered-existing-clip',recover_save)
            audit.update(succeeded=True,status='ok',local_path=str(path),phase='recovered',recovered_existing=True)
            return
        snapshot = await composer.apply_defaults(page, section='video',
            aspect_ligature=ASPECT_LIGATURE[Aspect.from_cli(request['aspect'])], count=1,
            model=VIDEO_MODEL_MENU_MATCHERS[VideoModel.OMNI_FLASH], confirm='always')
        try:
            if request.get('dry_run'):
                await page.screenshot(path=str(result_file.with_suffix('.prepared.png')))
                audit.update(phase='prepared', succeeded=False, dry_run=True)
                return
            prompt = (f"Generate exactly ONE new 10-second {request['aspect']} video with Gemini Omni Flash "
                "at native 720p. Do not use an edit-video operation. Do not generate images, variations, "
                "storyboards or multiple clips. Ask for approval of this one video only. "
                "The maximum permitted charge is 15 credits. "
                + ('Use the attached image as the precise initial frame, continuing that composition and motion. ' if request.get('initial_frame') else '')
                + request['prompt'])
            async def monitor():
                while True:
                    await asyncio.sleep(15)
                    try:
                        state = await page.evaluate(agent._VIDEO_JS)
                        audit['progress'] = {'pending_tiles':state['pending'],'finished_tiles':len(state['finished'])}
                        await page.screenshot(path=str(result_file.with_suffix('.progress.png')))
                        result_file.write_text(json.dumps(audit,indent=2))
                    except Exception:
                        pass
            def started(media_id):
                audit['media_id'] = media_id
                result_file.write_text(json.dumps(audit,indent=2))
            watcher = asyncio.create_task(monitor())
            try:
                (media,) = await composer.generate(page, prompt, kind='video', count=1, budget_s=900, on_first_media=started)
            finally:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
            audit.update(media_id=None if audit.get('opaque_media') else media.uuid, phase='downloading')
            result_file.write_text(json.dumps(audit, indent=2))
            async def save(media_id, content):
                path = Path(request['output'])
                path.write_bytes(content)
                return path
            path = await agent.download_video(page, media.src, media.uuid, save)
            audit.update(succeeded=True, status='ok', local_path=str(path), phase='complete')
            await page.screenshot(path=str(result_file.with_suffix('.completed.png')))
        except Exception:
            await page.screenshot(path=str(result_file.with_suffix('.failed.png')))
            buttons = await page.get_by_role('button').evaluate_all("els => els.map(e => ({text:e.innerText,aria:e.getAttribute('aria-label')}))")
            result_file.with_suffix('.buttons.json').write_text(json.dumps(buttons,indent=2))
            raise
        finally:
            await agent._restore_quietly(composer, page, snapshot)


def main():
    request_file, result_path = map(Path, sys.argv[1:3])
    request = json.loads(request_file.read_text())
    audit = {'status':'fail', 'succeeded':False, 'submitted':False, 'approved_credits':None, 'phase':'preparing'}
    code = 0
    try:
        asyncio.run(generate(request, audit, result_path))
    except Exception as exc:
        audit['error_message'] = str(exc)
        code = 1
    result_path.write_text(json.dumps(audit, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
