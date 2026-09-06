"""
项目级角色音色绑定服务（per-project voice binding）。

每个 ``character_id`` 在项目生命周期内绑一组 ``(vcn, speed, pitch, volume, emotion)``
+ 一组 MiniMax fallback 侧的 ``(minimax_voice_id, minimax_speed, minimax_pitch,
minimax_volume)``，跨章节复用。

独占规则：
- 主引擎（aliyun/xunfei）：同项目内 ``(vcn, speed, pitch)`` 三元组有 DB 唯一约束
  （``uq_pj_voice_slot``）。共用 vcn 的角色靠 ``speed``/``pitch`` 差异化区分。
- MiniMax fallback：同项目内 ``(minimax_voice_id, minimax_speed, minimax_pitch)``
  三元组在逻辑层独占（无 DB 约束，因 voice_id 池有限，参数差异化兜底）。优先
  按性别在 minimax voice_id 池里独占 voice_id；池用尽后叠 ``PARAM_GRID`` 参数槽。

旁白（无 ``character_id``）不参与绑定，走 ``tts_service`` 默认 speaker。

公开入口：
    binding = VoiceBindingService(db).get_or_allocate(
        project_id=1,
        character_id="abc123def456",
        character_name="李雷",
        gender="male", age="young",
        character_voice="冷静克制",
        emotion="calm",
    )
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger("voice_binding_service")

# MiniMax 默认参数（scale [0,100]，50 = 1.0×speed / 0 pitch / 50 volume）
_MINIMAX_DEFAULT_SPEED = 50
_MINIMAX_DEFAULT_PITCH = 50
_MINIMAX_DEFAULT_VOLUME = 50


class VoiceBindingService:
    """项目级角色音色绑定编排器。"""

    # 每个 vcn 的 (speed, pitch) 槽位枚举，按确定性顺序展开。
    # 第一个槽 (50, 50) 通常是 profile 默认，后续是差异化变体。
    # 5×5 = 25 槽/vcn，配合 5 个 vcn → 单项目支持 125 个不同角色。
    PARAM_GRID: List[Tuple[int, int]] = [
        (s, p)
        for s in (50, 55, 45, 52, 48)
        for p in (50, 55, 45, 52, 48)
    ]

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_or_allocate(
        self,
        project_id: int,
        character_id: str,
        character_name: str | None = None,
        gender: str | None = None,
        age: str | None = None,
        character_voice: str | None = None,
        emotion: str | None = None,
    ):
        """已绑定 → 返回现有 binding；未绑定 → 分配新槽位并持久化。

        分配优先级（让不同角色尽量听上去不同）：
        - **主引擎 vcn**：
          1. 基础 vcn 未占 → 直接用
          2. 同性别其他 vcn 未占 → 切换 vcn
          3. 同性别 vcn 全占 → 在基础 vcn 上按 ``PARAM_GRID`` 改 speed/pitch
        - **MiniMax voice_id**（同样的三级优先级，作用在 minimax voice_id 池上）

        Args:
            project_id: 项目 ID
            character_id: md5(name|project_id)[:12]
            character_name: 角色名（仅展示用）
            gender / age / character_voice / emotion: 透传给
                ``tts_service.match_voice_profile`` 拿基础 vcn + emotion。
        """
        from app.models import CharacterVoiceBinding
        from app.services.tts_service import tts_service

        # 1. 已绑定 → 直接返回（但若 minimax 字段缺失需 lazy 补全）
        existing = self._db.query(CharacterVoiceBinding).filter(
            CharacterVoiceBinding.project_id == project_id,
            CharacterVoiceBinding.character_id == character_id,
        ).first()
        if existing:
            if not existing.minimax_voice_id:
                self._lazy_fill_minimax(existing, project_id=project_id, gender=gender)
            return existing

        # 2. 通过现有 matcher 拿基础 vcn + emotion（30/20/10/15 打分）
        vcn, emo = tts_service.match_voice_profile(
            character_voice=character_voice,
            gender=gender, age=age, emotion=emotion,
        )

        # 3. 拿该 profile 的默认 speed/pitch/volume
        base = tts_service._lookup_profile_extras(vcn, emo)

        # 4. 主引擎 vcn 独占分配（基础 vcn / 同性别 vcn / 参数差异化）
        vcn, speed, pitch = self._allocate_primary_slot(
            project_id=project_id,
            base_vcn=vcn,
            gender=gender,
            base_speed=base["speed"],
            base_pitch=base["pitch"],
        )
        if vcn != tts_service.match_voice_profile(
            character_voice=character_voice, gender=gender, age=age, emotion=emotion,
        )[0]:
            # 切了 vcn → 重取新 vcn 的默认参数 base
            base = tts_service._lookup_profile_extras(vcn, emo)

        # 5. MiniMax voice_id 独占分配
        minimax_voice_id, minimax_speed, minimax_pitch = self._allocate_minimax_slot(
            project_id=project_id,
            base_speaker=vcn,
            gender=gender,
        )

        # 6. 写入（并发冲突 → UniqueConstraint 触发 IntegrityError → 重读）
        binding = CharacterVoiceBinding(
            project_id=project_id,
            character_id=character_id,
            character_name=character_name,
            vcn=vcn,
            speed=speed,
            pitch=pitch,
            volume=base["volume"],
            emotion=emo,
            minimax_voice_id=minimax_voice_id,
            minimax_speed=minimax_speed,
            minimax_pitch=minimax_pitch,
            minimax_volume=_MINIMAX_DEFAULT_VOLUME,
        )
        try:
            self._db.add(binding)
            self._db.commit()
            self._db.refresh(binding)
            logger.info(
                "voice_binding allocated project=%d character=%s(%s) → "
                "vcn=%s s=%d p=%d emo=%s | minimax=%s s=%d p=%d",
                project_id, character_name or "?", character_id[:8],
                vcn, speed, pitch, emo,
                minimax_voice_id, minimax_speed, minimax_pitch,
            )
        except IntegrityError:
            self._db.rollback()
            binding = self._db.query(CharacterVoiceBinding).filter(
                CharacterVoiceBinding.project_id == project_id,
                CharacterVoiceBinding.character_id == character_id,
            ).first()
            if binding is None:
                logger.warning(
                    "voice_binding IntegrityError but no existing row for character=%s, "
                    "retrying with next slot",
                    character_id[:8],
                )
                return self._retry_with_next_slot(
                    project_id, character_id, character_name,
                    vcn, emo, base, speed, pitch,
                    minimax_voice_id, minimax_speed, minimax_pitch,
                )
            if not binding.minimax_voice_id:
                self._lazy_fill_minimax(binding, project_id=project_id, gender=gender)
        return binding

    # ============================================================
    # 主引擎 vcn 分配
    # ============================================================

    def _allocate_primary_slot(
        self,
        *,
        project_id: int,
        base_vcn: str,
        gender: Optional[str],
        base_speed: int,
        base_pitch: int,
    ) -> Tuple[str, int, int]:
        """主引擎 vcn 独占分配。返回 (chosen_vcn, speed, pitch)。"""
        from app.models import CharacterVoiceBinding

        used_vcn = {
            row.vcn for row in self._db.query(CharacterVoiceBinding.vcn).filter(
                CharacterVoiceBinding.project_id == project_id,
            ).all()
        }
        if base_vcn not in used_vcn:
            chosen_vcn = base_vcn
        else:
            same_gender_vcn = self._same_gender_vcn_list(gender, exclude=base_vcn)
            unoccupied = [v for v in same_gender_vcn if v not in used_vcn]
            if unoccupied:
                chosen_vcn = unoccupied[0]
            else:
                chosen_vcn = base_vcn  # 全占 → 在 base_vcn 上参数差异化

        taken = {
            (row.speed, row.pitch)
            for row in self._db.query(
                CharacterVoiceBinding.speed, CharacterVoiceBinding.pitch
            ).filter(
                CharacterVoiceBinding.project_id == project_id,
                CharacterVoiceBinding.vcn == chosen_vcn,
            ).all()
        }
        candidates = [(base_speed, base_pitch)] + self.PARAM_GRID
        try:
            speed, pitch = next(sp for sp in candidates if sp not in taken)
        except StopIteration:
            logger.warning(
                "voice_binding primary param grid exhausted project=%d vcn=%s",
                project_id, chosen_vcn,
            )
            speed, pitch = base_speed, base_pitch
        return chosen_vcn, speed, pitch

    # ============================================================
    # MiniMax voice_id 分配
    # ============================================================

    def _allocate_minimax_slot(
        self,
        *,
        project_id: int,
        base_speaker: str,
        gender: Optional[str],
    ) -> Tuple[str, int, int]:
        """MiniMax voice_id 独占分配。返回 (voice_id, speed, pitch)。

        优先级：
        1. profile.fallback_voice_ids.minimax 给出的基础 voice_id 未占 → 用
        2. 同性别其他 minimax voice_id 未占 → 切换
        3. 同性别 voice_id 全占 → 在基础 voice_id 上叠 PARAM_GRID 参数
        """
        from app.models import CharacterVoiceBinding
        from app.services.tts_service import tts_service

        base_voice_id = tts_service._lookup_minimax_profile_fallback(base_speaker)
        if not base_voice_id:
            # MiniMax 作为主引擎时，旧 vcn 字段可能只是默认占位符，未必能反查
            # profile。优先从同性别 MiniMax 池取基础音色，避免第一个女性角色也
            # 被分配默认男声。
            gender_voice_ids = self._same_gender_minimax_voice_ids(
                gender,
                exclude="",
            )
            base_voice_id = (
                base_speaker
                if base_speaker in gender_voice_ids
                else (gender_voice_ids[0] if gender_voice_ids else self._minimax_default_voice_id())
            )

        used = {
            (row.minimax_voice_id, row.minimax_speed, row.minimax_pitch)
            for row in self._db.query(
                CharacterVoiceBinding.minimax_voice_id,
                CharacterVoiceBinding.minimax_speed,
                CharacterVoiceBinding.minimax_pitch,
            ).filter(
                CharacterVoiceBinding.project_id == project_id,
                CharacterVoiceBinding.minimax_voice_id.isnot(None),
            ).all()
        }
        used_voice_ids = {v for v, _, _ in used}

        # 1. 基础 voice_id 未占
        if base_voice_id not in used_voice_ids:
            chosen = base_voice_id
        else:
            # 2. 同性别其他 voice_id 未占
            same_gender_ids = self._same_gender_minimax_voice_ids(
                gender, exclude=base_voice_id,
            )
            unoccupied = [v for v in same_gender_ids if v not in used_voice_ids]
            if unoccupied:
                chosen = unoccupied[0]
            else:
                # 3. 全占 → 基础 voice_id 上参数差异化
                chosen = base_voice_id

        # 参数差异化（同 voice_id 已占时叠 PARAM_GRID）
        taken_for_chosen = {
            (s, p)
            for v, s, p in used
            if v == chosen and s is not None and p is not None
        }
        candidates = [(_MINIMAX_DEFAULT_SPEED, _MINIMAX_DEFAULT_PITCH)] + self.PARAM_GRID
        try:
            speed, pitch = next(sp for sp in candidates if sp not in taken_for_chosen)
        except StopIteration:
            logger.warning(
                "voice_binding minimax param grid exhausted project=%d voice_id=%s",
                project_id, chosen,
            )
            speed, pitch = _MINIMAX_DEFAULT_SPEED, _MINIMAX_DEFAULT_PITCH
        return chosen, speed, pitch

    def _lazy_fill_minimax(
        self,
        binding,
        *,
        project_id: int,
        gender: Optional[str],
    ) -> None:
        """旧 binding 没有 minimax 字段时，按当前 vcn 反查 + 独占规则补全。"""
        voice_id, speed, pitch = self._allocate_minimax_slot(
            project_id=project_id,
            base_speaker=binding.vcn,
            gender=gender,
        )
        binding.minimax_voice_id = voice_id
        binding.minimax_speed = speed
        binding.minimax_pitch = pitch
        binding.minimax_volume = _MINIMAX_DEFAULT_VOLUME
        try:
            self._db.commit()
            self._db.refresh(binding)
            logger.info(
                "voice_binding minimax lazy-filled project=%d character=%s → "
                "minimax=%s s=%d p=%d",
                project_id, (binding.character_id or "")[:8],
                voice_id, speed, pitch,
            )
        except IntegrityError:
            self._db.rollback()
            logger.warning(
                "voice_binding minimax lazy-fill IntegrityError project=%d character=%s",
                project_id, (binding.character_id or "")[:8],
            )

    def _retry_with_next_slot(
        self,
        project_id: int,
        character_id: str,
        character_name: str | None,
        vcn: str,
        emo: str,
        base: dict,
        failed_speed: int,
        failed_pitch: int,
        minimax_voice_id: str,
        minimax_speed: int,
        minimax_pitch: int,
    ):
        """uq_pj_voice_slot 撞车后的兜底重试：在 taken 上加刚失败的槽，重新挑一个。"""
        from app.models import CharacterVoiceBinding

        taken_plus = {(failed_speed, failed_pitch)}
        # 重读 taken（避免 stale）
        taken = {
            (row.speed, row.pitch)
            for row in self._db.query(
                CharacterVoiceBinding.speed, CharacterVoiceBinding.pitch
            ).filter(
                CharacterVoiceBinding.project_id == project_id,
                CharacterVoiceBinding.vcn == vcn,
            ).all()
        } | taken_plus
        candidates = [(base["speed"], base["pitch"])] + self.PARAM_GRID
        try:
            speed, pitch = next(sp for sp in candidates if sp not in taken)
        except StopIteration:
            speed, pitch = failed_speed, failed_pitch

        binding = CharacterVoiceBinding(
            project_id=project_id,
            character_id=character_id,
            character_name=character_name,
            vcn=vcn,
            speed=speed, pitch=pitch,
            volume=base["volume"], emotion=emo,
            minimax_voice_id=minimax_voice_id,
            minimax_speed=minimax_speed,
            minimax_pitch=minimax_pitch,
            minimax_volume=_MINIMAX_DEFAULT_VOLUME,
        )
        try:
            self._db.add(binding)
            self._db.commit()
            self._db.refresh(binding)
        except IntegrityError:
            self._db.rollback()
            raise RuntimeError(
                f"voice_binding double IntegrityError project={project_id} "
                f"character={character_id} vcn={vcn} s={speed} p={pitch}"
            )
        return binding

    # ============================================================
    # 工具：同性别池派生
    # ============================================================

    @staticmethod
    def _same_gender_vcn_list(gender: str | None, exclude: str) -> List[str]:
        """从 ``tts_voice_profiles.json`` 派生同引擎、同性别 vcn 列表（去重、保序）。"""
        from app.services.tts_service import tts_service

        if not gender:
            return []
        g = gender.lower()
        out: List[str] = []
        exclude_engine = None
        for p in tts_service.voice_profiles:
            v = p.get("vcn") or p.get("speaker")
            if v == exclude:
                exclude_engine = tts_service._profile_engine(p)
                break
        for p in tts_service.voice_profiles:
            if not isinstance(p, dict):
                continue
            if (p.get("gender") or "").lower() != g:
                continue
            if exclude_engine and tts_service._profile_engine(p) != exclude_engine:
                continue
            v = p.get("vcn") or p.get("speaker")
            if v and v != exclude and v not in out:
                out.append(v)
        return out

    @staticmethod
    def _same_gender_minimax_voice_ids(
        gender: str | None,
        exclude: str,
    ) -> List[str]:
        """从 ``tts_voice_profiles.json`` 派生同性别 minimax voice_id 池（去重、保序）。

        用于"基础 voice_id 已占，找同性别其他未占用 voice_id"。
        """
        from app.services.tts_service import tts_service

        if not gender:
            return []
        g = gender.lower()
        out: List[str] = []
        for p in tts_service.voice_profiles:
            if not isinstance(p, dict):
                continue
            if (p.get("gender") or "").lower() != g:
                continue
            fb = p.get("fallback_voice_ids") or {}
            if not isinstance(fb, dict):
                continue
            v = fb.get("minimax")
            if v and isinstance(v, str) and v.strip() and v != exclude and v not in out:
                out.append(v.strip())
        return out

    @staticmethod
    def _minimax_default_voice_id() -> str:
        """取 MiniMax 默认 voice_id（从 provider 配置读，避免循环 import 缓存）。"""
        try:
            from app.services.minimax_tts_provider import MINIMAX_TTS_DEFAULT_VOICE_ID
            return MINIMAX_TTS_DEFAULT_VOICE_ID or "male-qn-qingse"
        except Exception:
            return "male-qn-qingse"
