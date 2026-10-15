"""翻译模式选项 API：深度/轻量/纯批量预设 + 细粒度开关（存 project_meta）"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import require_project
from ..errors import ApiError
from ..state import AppState

router = APIRouter(tags=['translate-options'])


def _snapshot(state: AppState) -> dict:
    from translate_options import parse_options
    opts = parse_options(state.db.get_meta('translate_options'))
    return {'preset': opts.preset, 'flags': opts.flags()}


@router.get('/current/translate-options')
async def get_translate_options(state: AppState = Depends(require_project)):
    return await state.run_sync(_snapshot, state)


class OptionsIn(BaseModel):
    # 只给 preset（deep/light/bulk）：套用整档；只给 flags：在当前基础上
    # 微调并置 preset=custom。两者都给等同于只给 flags。
    preset: Optional[str] = None
    flags: Optional[dict] = None


@router.put('/current/translate-options')
async def update_translate_options(req: OptionsIn,
                                   state: AppState = Depends(require_project)):
    def _set():
        from translate_options import (PRESETS, TranslateOptions,
                                       options_from_preset, parse_options,
                                       validate_flags)
        if req.flags is None:
            if not req.preset or req.preset not in PRESETS:
                raise ApiError(400, 'BAD_PRESET',
                               f'preset 必须是 {"/".join(PRESETS)}')
            opts = options_from_preset(req.preset)
        else:
            merged = parse_options(
                state.db.get_meta('translate_options')).flags()
            try:
                merged.update(validate_flags(req.flags))
            except ValueError as e:
                raise ApiError(400, 'BAD_FLAGS', str(e)) from e
            opts = TranslateOptions(preset='custom', **merged)
        state.db.set_meta('translate_options', opts.to_json())
        return {'preset': opts.preset, 'flags': opts.flags()}

    return await state.run_sync(_set)
