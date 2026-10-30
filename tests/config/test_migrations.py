"""配置迁移：旧布局 → 加载 → 落盘效果 + 版本推进到钩子链尾；二次加载零漂移零写回

每个用例给出"怎样把默认配置拨回旧布局"（seed）与"迁移后应是什么样"（check）；
两条通用断言（落盘+推进、幂等）对所有用例参数化执行。钩子链尾版本从注册表取，不硬编码版本串。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest
import tomlkit
from pydantic import ValidationError

from src.modules.config.model_schemas import LLMModelConfig
from src.modules.config.multi_file_loader import generate_default_configs, get_config_version, load_config_dir
from src.modules.config.upgrade import (
    _FILE_HOOKS,
    _drop_minecraft_max_steps,
    _drop_simulator_llm_profile,
    _upgrade_builder_scene_transport,
    _upgrade_minecraft_context_tokens,
    advance_file_versions,
)
from tests.config.helpers import read_doc, rewind_and_append, tail_version, write_doc


@dataclass
class Case:
    id: str
    files: tuple[str, ...]  # 迁移后应推进到链尾版本的文件
    seed: Callable[[Path], None]
    check: Callable[[Path], None]


def _rewound(config_dir: Path, file_name: str, version: str) -> tomlkit.TOMLDocument:
    doc = read_doc(config_dir, file_name)
    doc["meta"]["version"] = version
    return doc


# agents.toml


def _seed_force(d: Path) -> None:
    doc = _rewound(d, "agents.toml", "2.0.31")
    force = doc["agents"]["streamer"]["force"]
    for key in list(force):
        del force[key]
    force["force_data_types"] = ["super_chat", "guard", "gift"]
    force["force_importance"] = 0.8
    write_doc(d, "agents.toml", doc)


def _check_force(d: Path) -> None:
    force = read_doc(d, "agents.toml")["agents"]["streamer"]["force"]
    assert "force_data_types" not in force and "force_importance" not in force  # 正名 + 零消费者死字段清除
    assert force["force_message_types"] == ["super_chat", "guard", "gift"]


def _seed_text_adv(d: Path) -> None:
    doc = _rewound(d, "agents.toml", "2.0.33")
    old = tomlkit.table()
    old["engine_kind"], old["decision_strategy"], old["enable_event_emission"] = "text_adv", "first_option", True
    doc["agents"]["text_adv"] = old
    write_doc(d, "agents.toml", doc)


def _check_text_adv(d: Path) -> None:
    text_adv = read_doc(d, "agents.toml")["agents"]["text_adv"]
    assert not {"engine_kind", "decision_strategy", "enable_event_emission"} & set(text_adv)  # 三个假旋钮删除
    assert (text_adv["monitor_index"], text_adv["stability_sample_ms"], text_adv["stability_consecutive"]) == (
        1,
        150,
        2,
    )
    assert dict(text_adv["keys"]) == {"advance": "space", "skip": "ctrl", "menu": "backspace"}  # 新字段补默认值
    assert "region" not in text_adv and "auto_button_xy" not in text_adv  # None 默认字段 TOML 不可表达，落盘缺省


def _seed_window_threshold(d: Path) -> None:
    doc = _rewound(d, "agents.toml", "2.0.32")
    doc["agents"]["streamer"]["background"]["window_event_threshold"] = 200
    write_doc(d, "agents.toml", doc)


def _check_window_threshold(d: Path) -> None:
    assert "window_event_threshold" not in read_doc(d, "agents.toml")["agents"]["streamer"]["background"]


# infra.toml


def _seed_events_persist(d: Path) -> None:
    doc = _rewound(d, "infra.toml", "2.0.32")
    doc["events"]["history_size"] = 5000
    doc["events"]["persist"] = False
    write_doc(d, "infra.toml", doc)


def _check_events_persist(d: Path) -> None:
    events = read_doc(d, "infra.toml")["events"]
    assert "persist" not in events and events["history_size"] == 5000


def _seed_sc_probability(d: Path) -> None:
    doc = _rewound(d, "infra.toml", "2.0.40")
    doc["simulator"]["sc_probability"] = 0.01
    write_doc(d, "infra.toml", doc)


def _seed_widget_html_page(d: Path) -> None:
    doc = _rewound(d, "infra.toml", "2.0.42")
    for section in ("danmaku_widget", "subtitle_widget", "game2048_widget"):
        doc["dashboard"][section]["enable_html_page"] = True
    write_doc(d, "infra.toml", doc)


def _check_widget_html_page(d: Path) -> None:
    dashboard = read_doc(d, "infra.toml")["dashboard"]
    for section in ("danmaku_widget", "subtitle_widget", "game2048_widget"):
        assert "enable_html_page" not in dashboard[section]
    assert dashboard["danmaku_widget"]["enabled"] is True  # 段内其余字段原样保留


def _check_sc_probability(d: Path) -> None:
    simulator = read_doc(d, "infra.toml")["simulator"]
    assert "sc_probability" not in simulator and "sc_pay_probability" in simulator  # 实际生效的付费 SC 概率不受影响


def _seed_simulator_llm_profile(d: Path) -> None:
    doc = _rewound(d, "infra.toml", "2.0.31")
    doc["simulator"]["llm_profile"] = "simulator"
    doc["simulator"]["llm_temperature"] = 0.9
    write_doc(d, "infra.toml", doc)


def _check_simulator_llm_profile(d: Path) -> None:
    simulator = read_doc(d, "infra.toml")["simulator"]
    assert "llm_profile" not in simulator and simulator["llm_temperature"] == 0.9  # 绑定改代码常量声明，字段随之移除


def _seed_logging_directory(value: str) -> Callable[[Path], None]:
    def seed(d: Path) -> None:
        doc = _rewound(d, "infra.toml", "2.0.41")
        doc["logging"]["directory"] = value
        write_doc(d, "infra.toml", doc)

    return seed


def _check_logging_directory(expected: str) -> Callable[[Path], None]:
    def check(d: Path) -> None:
        assert read_doc(d, "infra.toml")["logging"]["directory"] == expected

    return check


# model.toml


def _seed_profile_history_budget(d: Path) -> None:
    doc = _rewound(d, "model.toml", "2.0.43")
    for name, budget in (("planner", 8000), ("replyer", 128000), ("minecraft", 0)):
        doc["llm_profiles"][name]["history_token_budget"] = budget
    doc["llm_profiles"]["planner"]["temperature"] = 0.9
    write_doc(d, "model.toml", doc)


def _seed_profile_give_up(d: Path) -> None:
    doc = _rewound(d, "model.toml", "2.0.44")
    for profile in doc["llm_profiles"].values():
        profile.pop("give_up_ms", None)
    doc["llm_profiles"]["planner"]["give_up_ms"] = 0  # 用户显式写过的值（包括一直等）不动
    write_doc(d, "model.toml", doc)


def _check_profile_give_up(d: Path) -> None:
    profiles = read_doc(d, "model.toml")["llm_profiles"]
    assert profiles["replyer"]["give_up_ms"] == 60_000  # 直播台词卡住不再冷场
    assert profiles["planner"]["give_up_ms"] == 0
    assert profiles["summary"]["give_up_ms"] == 0  # 其余用途由漂移写回补通用默认：一直等


def _check_profile_history_budget(d: Path) -> None:
    profiles = read_doc(d, "model.toml")["llm_profiles"]
    assert all(
        "history_token_budget" not in profile for profile in profiles.values()
    )  # 改由各 Agent 上下文段按窗口比例换算
    assert profiles["planner"]["temperature"] == 0.9


# tools.toml 与跨文件迁移


def _seed_warudo_subtitle(d: Path) -> None:
    rewind_and_append(
        d,
        "tools.toml",
        "2.0.36",
        "\n[tools.avatar.warudo]\nenabled = true\n\n[tools.avatar.warudo.config]\n"
        "ws_port = 19190\nsubtitle_enabled = true\nsubtitle_port = 8766\nsubtitle_show_status = true\n",
    )


def _check_warudo_subtitle(d: Path) -> None:
    # 删键钩子（2.0.37）先跑，迁移钩子（2.0.38）随后把残段整体迁往 avatar.toml，存活键落在 [platform.warudo]
    assert "avatar" not in read_doc(d, "tools.toml")["tools"]
    warudo = read_doc(d, "avatar.toml")["platform"]["warudo"]
    assert not {"subtitle_enabled", "subtitle_port", "subtitle_show_status"} & set(warudo)
    assert warudo["ws_port"] == 19190


def _seed_vts_lipsync(d: Path) -> None:
    rewind_and_append(
        d,
        "tools.toml",
        "2.0.32",
        "\n[tools.avatar.vts]\nenabled = true\n\n[tools.avatar.vts.config]\n"
        "lip_sync_enabled = false\nsample_rate = 22050\nvolume_threshold = 0.03\nmax_mouth_open = 0.75\nmin_mouth_delta = 0.008\n",
    )


def _check_vts_lipsync(d: Path) -> None:
    avatar = read_doc(d, "avatar.toml")
    lipsync = avatar["lipsync"]  # 目标随 avatar 迁出直接写 avatar.toml
    assert lipsync["enabled"] is False  # 开关键正名
    assert (lipsync["sample_rate"], lipsync["volume_threshold"], lipsync["max_mouth_open"]) == (22050, 0.03, 0.75)
    assert lipsync["min_mouth_delta"] == 0.008  # 用户显式值原样搬迁
    assert "avatar" not in read_doc(d, "tools.toml")["tools"] and "vts" in avatar["platform"]["enabled"]
    assert "avatar" not in read_doc(d, "infra.toml")  # infra 侧零残留


OLD_TOOLS_AVATAR = (
    "\n[tools.avatar.vts]\nenabled = true\n\n[tools.avatar.vts.config]\n"
    'type = "vts"\nvts_host = "127.0.0.1"\nvts_port = 8001\nidle_extra_params = { "SleeveL" = 0.3 }\n'
    "\n[tools.avatar.warudo]\nenabled = false\n\n[tools.avatar.warudo.config]\n"
    'type = "warudo"\nws_port = 19190\n'
)


def _seed_avatar_platform(d: Path) -> None:
    rewind_and_append(d, "tools.toml", "2.0.37", OLD_TOOLS_AVATAR)


def _check_avatar_platform(d: Path) -> None:
    platform = read_doc(d, "avatar.toml")["platform"]
    assert list(platform["enabled"]) == ["vts"]  # enabled 布尔换算为启用名单，仅 true 成员进名单
    vts = platform["vts"]  # 成员段去 .config 层直接铺键，用户显式值保真
    assert (vts["vts_host"], vts["vts_port"]) == ("127.0.0.1", 8001)
    assert (
        platform["warudo"]["ws_port"] == 19190 and "warudo" not in platform["enabled"]
    )  # false 成员配置保留但不进名单
    assert "idle_extra_params" not in vts and "base_smile" not in vts  # 待机键随后由面捕合成器接管而退役（2.0.42）
    assert vts["eye_open_rest"] == 0.55  # 缺省键由 Schema 补默认
    assert "avatar" not in read_doc(d, "tools.toml")["tools"]


def _seed_avatar_lipsync_and_idle(d: Path) -> None:
    old_bindings = "".join(
        f'idle_param_{part}_{axis} = "{name}"\n'
        for part, axes in (("head", ("HeadAngleX", "HeadAngleY", "HeadAngleZ")), ("body", ("BodyX", "BodyY", "BodyZ")))
        for axis, name in zip("xyz", axes, strict=True)
    )
    tools_text = OLD_TOOLS_AVATAR.replace(
        'idle_extra_params = { "SleeveL" = 0.3 }\n', 'idle_extra_params = { "SleeveL" = 0.3 }\n' + old_bindings, 1
    )
    rewind_and_append(d, "tools.toml", "2.0.37", tools_text)
    rewind_and_append(
        d,
        "infra.toml",
        "2.0.37",
        '\n[avatar]\n\n[avatar.lipsync]\ntype = "lipsync"\nenabled = true\nsample_rate = 22050\n',
    )


def _check_avatar_lipsync_and_idle(d: Path) -> None:
    avatar = read_doc(d, "avatar.toml")
    assert (avatar["lipsync"]["sample_rate"], avatar["lipsync"]["enabled"]) == (
        22050,
        True,
    )  # infra 的 lipsync 整段搬迁
    assert "avatar" not in read_doc(d, "infra.toml")
    vts = avatar["platform"]["vts"]  # idle 绑定先在 2.0.39 改写为新默认，再随 2.0.42 整组退役
    assert not [key for key in vts if key.startswith("idle_")]
    assert vts["vts_host"] == "127.0.0.1"


def _seed_warudo_face_keys(d: Path) -> None:
    doc = _rewound(d, "avatar.toml", "2.0.43")
    warudo = doc["platform"]["warudo"]
    warudo["ws_port"] = 19191
    warudo["talking_head_enabled"] = False
    warudo["talking_head_interval"] = 0.2
    warudo["expression"] = {"emotions": {"happy": {"smile": 1.0}}, "mouth_keys": ["smile"]}
    write_doc(d, "avatar.toml", doc)


def _check_warudo_face_keys(d: Path) -> None:
    warudo = read_doc(d, "avatar.toml")["platform"]["warudo"]
    # 脸改由面捕合成器经 iFacialMocap 驱动：表情档案与说话点头的键退役，其余照旧
    assert not {"expression", "talking_head_enabled", "talking_head_interval"} & set(warudo)
    assert warudo["ws_port"] == 19191 and warudo["tracking_port"] == 49983


def _seed_vts_idle(idle_enabled: bool, explicit_wander: bool = False) -> Callable[[Path], None]:
    def seed(d: Path) -> None:
        doc = _rewound(d, "avatar.toml", "2.0.41")
        vts = doc["platform"]["vts"]
        vts["base_smile"] = 0.4
        vts["idle_enabled"] = idle_enabled
        vts["idle_param_head_x"] = "FaceAngleX"
        vts["idle_head_amplitude"] = 0.08
        vts["idle_extra_params"] = {"SleeveL": 0.3}
        if explicit_wander:  # 用户已在新段里显式写过头部微动开关
            doc["tracking"]["head_wander_enabled"] = True
        else:  # 拨回旧布局：还没有面捕合成器段
            del doc["tracking"]
        write_doc(d, "avatar.toml", doc)

    return seed


def _check_vts_idle(head_wander: bool) -> Callable[[Path], None]:
    def check(d: Path) -> None:
        avatar = read_doc(d, "avatar.toml")
        vts = avatar["platform"]["vts"]
        assert "base_smile" not in vts and not [key for key in vts if key.startswith("idle_")]
        assert avatar["tracking"]["head_wander_enabled"] is head_wander

    return check


def _seed_old_recipes(d: Path) -> None:
    doc = _rewound(d, "avatar.toml", "2.0.42")
    doc["tracking"]["recipes"] = {
        "happy": {"eye_open": -1.0, "mouth_smile": 1.0, "mouth_open": 0.3, "brow": 0.3, "head_pitch": 2.0},
        "angry": {"brow": -0.7, "angry": 0.6, "mouth_smile": -0.3, "motion": 1.2},
        "shy": {"mouthSmile": 0.4},  # 已是规范名：原样保留
    }
    write_doc(d, "avatar.toml", doc)


def _check_arkit_recipes(d: Path) -> None:
    recipes = read_doc(d, "avatar.toml")["tracking"]["recipes"]
    assert dict(recipes["happy"]) == {
        "eyeBlink": 1.0,
        "mouthSmile": 1.0,
        "jawOpen": 0.3,
        "browInnerUp": 0.3,
        "browOuterUp": 0.3,
        "head_pitch": 2.0,
    }
    # 压眉由旧的"眉往下"和"生气"累加；motion 名字没变
    assert dict(recipes["angry"]) == {"browDown": 1.0, "noseSneer": 0.24, "mouthFrown": 0.3, "motion": 1.2}
    assert dict(recipes["shy"]) == {"mouthSmile": 0.4}


# collectors.toml


def _seed_minecraft_companions(d: Path) -> None:
    doc = _rewound(d, "collectors.toml", "2.0.40")
    doc["enabled"] = ["console_input", "maicraft_attention", "maicraft_chat"]
    doc["maicraft_attention"] = {
        "url": "http://127.0.0.1:8766/mcp",
        "transport": "http",
        "retry_interval_ms": 5000,
        "wait_ms": 25000,
    }
    doc["maicraft_chat"] = {"url": "http://192.168.1.9:8766/mcp", "retry_interval_ms": 9000}
    write_doc(d, "collectors.toml", doc)


def _check_minecraft_companions(d: Path) -> None:
    doc = read_doc(d, "collectors.toml")  # 附属采集器改随 Minecraft Agent 开关，配置由 [agents.minecraft] 换算
    assert list(doc["enabled"]) == ["console_input"]
    assert "maicraft_attention" not in doc and "maicraft_chat" not in doc


def _seed_bili_danmaku_message_config(d: Path) -> None:
    doc = _rewound(d, "collectors.toml", "2.0.43")
    doc["bili_danmaku"]["room_id"] = 12345
    doc["bili_danmaku"]["message_config"] = {"default_user_id": "guest", "platform": "bilibili"}
    write_doc(d, "collectors.toml", doc)


def _check_bili_danmaku_message_config(d: Path) -> None:
    section = read_doc(d, "collectors.toml")["bili_danmaku"]  # 缺 uid 改为丢弃、平台写死，整段退役
    assert "message_config" not in section
    assert section["room_id"] == 12345  # 同段其他配置原样保留


CASES = [
    Case("force-field-rename", ("agents.toml",), _seed_force, _check_force),
    Case("text-adv-fake-knobs", ("agents.toml",), _seed_text_adv, _check_text_adv),
    Case("window-event-threshold", ("agents.toml",), _seed_window_threshold, _check_window_threshold),
    Case("events-persist", ("infra.toml",), _seed_events_persist, _check_events_persist),
    Case("sc-probability", ("infra.toml",), _seed_sc_probability, _check_sc_probability),
    Case("simulator-llm-profile", ("infra.toml",), _seed_simulator_llm_profile, _check_simulator_llm_profile),
    Case(
        "logging-directory-old-default",
        ("infra.toml",),
        _seed_logging_directory("logs"),
        _check_logging_directory("logs/app"),
    ),
    Case(
        "logging-directory-user-value-kept",
        ("infra.toml",),
        _seed_logging_directory("E:/my-logs"),
        _check_logging_directory("E:/my-logs"),  # hook 仅改写旧默认落盘值，用户显式自设的目录原样保留
    ),
    Case("profile-history-budget", ("model.toml",), _seed_profile_history_budget, _check_profile_history_budget),
    Case("profile-give-up", ("model.toml",), _seed_profile_give_up, _check_profile_give_up),
    Case("warudo-subtitle-keys", ("tools.toml", "avatar.toml"), _seed_warudo_subtitle, _check_warudo_subtitle),
    Case("vts-lipsync-to-avatar", ("tools.toml", "infra.toml"), _seed_vts_lipsync, _check_vts_lipsync),
    Case("avatar-platform-graduation", ("tools.toml",), _seed_avatar_platform, _check_avatar_platform),
    Case(
        "avatar-lipsync-and-idle-bindings",
        ("tools.toml", "infra.toml"),
        _seed_avatar_lipsync_and_idle,
        _check_avatar_lipsync_and_idle,
    ),
    Case(
        "vts-idle-retired-to-tracking",
        ("avatar.toml",),
        _seed_vts_idle(idle_enabled=False),
        _check_vts_idle(head_wander=False),  # 关过待机微动：转成面捕合成器的头部微动开关
    ),
    Case("vts-idle-on-retired", ("avatar.toml",), _seed_vts_idle(idle_enabled=True), _check_vts_idle(True)),
    Case(
        "vts-idle-explicit-tracking-kept",
        ("avatar.toml",),
        _seed_vts_idle(idle_enabled=False, explicit_wander=True),
        _check_vts_idle(True),  # 新段里用户显式写的值优先，旧开关不覆盖
    ),
    Case("tracking-recipes-to-arkit", ("avatar.toml",), _seed_old_recipes, _check_arkit_recipes),
    Case("warudo-face-keys-retired", ("avatar.toml",), _seed_warudo_face_keys, _check_warudo_face_keys),
    Case(
        "minecraft-companion-collectors", ("collectors.toml",), _seed_minecraft_companions, _check_minecraft_companions
    ),
    Case(
        "bili-danmaku-message-config",
        ("collectors.toml",),
        _seed_bili_danmaku_message_config,
        _check_bili_danmaku_message_config,
    ),
    Case("widget-html-page", ("infra.toml",), _seed_widget_html_page, _check_widget_html_page),
]


# 钩子 → 用例对账表：升级注册表里每个钩子必须被 CASES 中的用例或某个具名测试函数覆盖，
# 否则该钩子的迁移行为（含幂等与写回）没有任何验证兜底。覆盖定位分两类：
#   ("case", <Case.id>)     —— 覆盖体是本文件 CASES 用例表中的用例；
#   ("test", "<文件名>#<测试函数名>") —— 覆盖体是 tests/config 下的具名测试函数。
HOOK_COVERAGE: dict[str, tuple[str, str]] = {
    # CASES 用例表覆盖
    "agents_force_field_rename": ("case", "force-field-rename"),
    "infra_drop_events_persist": ("case", "events-persist"),
    "drop_simulator_llm_profile": ("case", "simulator-llm-profile"),
    "drop_window_event_threshold": ("case", "window-event-threshold"),
    "drop_text_adv_fake_knobs": ("case", "text-adv-fake-knobs"),
    "drop_simulator_sc_probability": ("case", "sc-probability"),
    "logging_directory_subdir": ("case", "logging-directory-old-default"),
    "vts_lipsync_to_avatar": ("case", "vts-lipsync-to-avatar"),
    "drop_warudo_subtitle_keys": ("case", "warudo-subtitle-keys"),
    "avatar_platform_to_avatar_file": ("case", "avatar-platform-graduation"),
    "avatar_lipsync_to_avatar_file": ("case", "avatar-lipsync-and-idle-bindings"),
    "retire_warudo_face_keys": ("case", "warudo-face-keys-retired"),
    "vts_idle_to_tracking": ("case", "vts-idle-retired-to-tracking"),
    "tracking_recipes_to_arkit": ("case", "tracking-recipes-to-arkit"),
    "drop_minecraft_companion_collectors": ("case", "minecraft-companion-collectors"),
    "drop_bili_danmaku_message_config": ("case", "bili-danmaku-message-config"),
    "drop_profile_history_budget": ("case", "profile-history-budget"),
    "profile_give_up": ("case", "profile-give-up"),
    "widget_drop_html_page": ("case", "widget-html-page"),
    # 具名测试函数覆盖
    "builder_scene_transport": ("test", "test_migrations.py#test_builder_scene_transport_hook_and_writeback"),
    "drop_minecraft_builder_section": ("test", "test_migrations.py#test_builder_scene_transport_hook_and_writeback"),
    "drop_minecraft_max_steps": (
        "test",
        "test_minecraft_step_limit_migration.py#test_step_limit_removal_is_written_back_only_to_agents",
    ),
    "normalize_llm_model_cache_mode": ("test", "test_migrations.py#test_llm_model_cache_switch_normalized_to_enum"),
    "minecraft_context_tokens": (
        "test",
        "test_minecraft_context_tokens_migration.py#test_char_budget_converted_and_written_back",
    ),
    "drop_generation_limits": (
        "test",
        "test_remove_llm_limits.py#test_legacy_limits_removed_from_disk_only_for_affected_file",
    ),
    "drop_input_limits": (
        "test",
        "test_remove_llm_limits.py#test_legacy_limits_removed_from_disk_only_for_affected_file",
    ),
    "drop_simulator_text_limits": (
        "test",
        "test_remove_llm_limits.py#test_legacy_limits_removed_from_disk_only_for_affected_file",
    ),
    "drop_tool_input_limits": (
        "test",
        "test_remove_llm_limits.py#test_legacy_limits_removed_from_disk_only_for_affected_file",
    ),
    "drop_memory_injection_limit": (
        "test",
        "test_remove_llm_limits.py#test_legacy_limits_removed_from_disk_only_for_affected_file",
    ),
    "drop_tools_memory_section": (
        "test",
        "test_tools_memory_section_migration.py#test_tools_memory_section_dropped_and_written_back",
    ),
    "web_search_bing_entry_default_swap": (
        "test",
        "test_web_search_bing_entry_migration.py#test_old_default_entry_rewritten_and_version_advanced",
    ),
    "replyer_style_drop_emoji": (
        "test",
        "test_replyer_style_emoji_migration.py#test_old_default_rewritten_and_version_advanced",
    ),
    "retire_live_frame_gates": (
        "test",
        "test_live_frame_gates_retire_migration.py#test_live_frame_gates_removed_and_written_back",
    ),
    "widen_recent_intents_window": (
        "test",
        "test_recent_intents_window_migration.py#test_old_default_rewritten_and_written_back",
    ),
    "retire_streamer_narrative": (
        "test",
        "test_streamer_narrative_retire_migration.py#test_narrative_section_removed_and_written_back",
    ),
    "drop_background_cold_timeout": (
        "test",
        "test_background_cold_timeout_migration.py#test_background_cold_timeout_dropped_and_written_back",
    ),
    "drop_persona_audience_salutation": (
        "test",
        "test_audience_salutation_retire_migration.py#test_persona_audience_salutation_dropped_and_written_back",
    ),
}


def test_every_registered_upgrade_hook_is_covered_by_a_case_or_named_test() -> None:
    """注册表每个钩子都在对账表里，对账表每项都指向真实存在的用例或测试函数。

    新登记钩子而漏写对账 → 本测试红；删除钩子而漏清对账 → 同样红（防对账表腐烂）。
    """
    registered = {hook.name for hooks in _FILE_HOOKS.values() for hook in hooks}
    assert registered == set(HOOK_COVERAGE), (
        "升级钩子注册表与用例对账表不一致：\n"
        f"  注册了但无覆盖记录（新钩子须补对账或补用例）: {sorted(registered - set(HOOK_COVERAGE))}\n"
        f"  对账表里有但注册表已无（须清理陈旧条目）: {sorted(set(HOOK_COVERAGE) - registered)}"
    )
    case_ids = {case.id for case in CASES}
    for name, (kind, locator) in HOOK_COVERAGE.items():
        if kind == "case":
            assert locator in case_ids, f"钩子 {name} 对账指向不存在的用例 {locator!r}"
        else:
            file_name, test_name = locator.split("#")
            path = Path(__file__).parent / file_name
            assert path.exists() and f"def {test_name}" in path.read_text(encoding="utf-8"), (
                f"钩子 {name} 对账指向不存在的测试 {locator!r}"
            )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_old_layout_is_migrated_written_back_and_versions_advance_to_chain_tail(case: Case, tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    case.seed(tmp_path)
    load_config_dir(tmp_path)
    case.check(tmp_path)
    for file_name in case.files:  # 版本流按文件独立：只推进到本文件钩子链尾，不随其他文件前进
        assert get_config_version(tmp_path, file_name) == tail_version(file_name)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_second_load_has_no_drift_and_rewrites_nothing(case: Case, tmp_path: Path) -> None:
    generate_default_configs(tmp_path)
    case.seed(tmp_path)
    load_config_dir(tmp_path)
    snapshot = {path.name: path.read_bytes() for path in tmp_path.glob("*.toml")}
    _, report = load_config_dir(tmp_path)
    assert not report.has_drift
    assert {path.name: path.read_bytes() for path in tmp_path.glob("*.toml")} == snapshot


# 钩子本体与特殊场景


def test_simulator_llm_profile_hook_is_idempotent_and_scheduler_advances_version() -> None:
    data = {"simulator": {"enabled": True, "llm_profile": "simulator", "llm_temperature": 0.9}}
    assert _drop_simulator_llm_profile(data) == ["simulator.llm_profile"]
    assert "llm_profile" not in data["simulator"] and data["simulator"]["llm_temperature"] == 0.9
    assert _drop_simulator_llm_profile(data) == [] and _drop_simulator_llm_profile({"simulator": {}}) == []
    assert _drop_simulator_llm_profile({}) == []

    raw = {"meta": {"version": "2.0.31"}, "simulator": {"llm_profile": "simulator"}}
    changed = advance_file_versions({"infra.toml": raw})
    assert {"simulator.llm_profile", "meta.version"} <= set(changed["infra.toml"])
    assert raw["meta"]["version"] == tail_version("infra.toml")  # 推进到本文件钩子链尾，与全局基线种子无关


def test_minecraft_step_limit_hook_keeps_other_limits() -> None:
    minecraft = {"max_steps": 50, "execute_poll_interval_ms": 4000, "builder": {"max_steps": 9}}
    data = {"agents": {"minecraft": minecraft}}
    assert _drop_minecraft_max_steps(data) == ["agents.minecraft.max_steps"]
    assert minecraft == {
        "execute_poll_interval_ms": 4000,
        "builder": {"max_steps": 9},
    }  # 子设计任务与后台查询仍用各自配置
    assert _drop_minecraft_max_steps(data) == [] and _drop_minecraft_max_steps({}) == []


@pytest.mark.parametrize("old_version", ["2.0.35", "2.0.38"])
@pytest.mark.parametrize("old_limit", [50, 120])
def test_minecraft_step_limit_is_removed_from_agents_only(tmp_path: Path, old_version: str, old_limit: int) -> None:
    generate_default_configs(tmp_path)
    load_config_dir(tmp_path)
    doc = _rewound(tmp_path, "agents.toml", old_version)
    doc["agents"]["minecraft"]["max_steps"] = old_limit
    doc["agents"]["minecraft"]["context"]["recent_turns"] = 9
    write_doc(tmp_path, "agents.toml", doc)
    others = {path: path.read_bytes() for path in tmp_path.glob("*.toml") if path.name != "agents.toml"}

    load_config_dir(tmp_path)

    minecraft = read_doc(tmp_path, "agents.toml")["agents"]["minecraft"]
    assert "max_steps" not in minecraft and minecraft["context"]["recent_turns"] == 9  # 存量默认值与自定义上限都移除
    assert all(path.read_bytes() == content for path, content in others.items())  # 其余配置文件保持原样


def test_builder_scene_transport_hook_and_writeback(tmp_path: Path) -> None:
    builder = {"validate_tool": "builder_validate", "preview_tool": "", "execute_tool": "custom_execute"}
    data = {"agents": {"minecraft": {"builder": builder}}}
    assert _upgrade_builder_scene_transport(data)
    assert builder == {"execute_tool": "custom_execute"}  # 已有自定义受理工具名仍由用户配置决定
    assert _upgrade_builder_scene_transport(data) == []

    custom = {"validate_tool": "my_validate", "preview_tool": "", "execute_tool": "builder_execute"}
    with pytest.raises(ValueError, match="自定义旧协议"):  # 自定义旧协议不猜测替换，报错时原配置保持完整
        _upgrade_builder_scene_transport({"agents": {"minecraft": {"builder": custom}}})
    assert custom["validate_tool"] == "my_validate"

    generate_default_configs(tmp_path)
    agents = _rewound(tmp_path, "agents.toml", "2.0.34")
    agents["agents"]["minecraft"]["builder"] = {
        "validate_tool": "builder_validate",
        "preview_tool": "",
        "execute_tool": "builder_execute",
        "max_steps": 9,
    }
    write_doc(tmp_path, "agents.toml", agents)
    write_doc(tmp_path, "model.toml", _rewound(tmp_path, "model.toml", "2.0.34"))
    load_config_dir(tmp_path)
    assert (
        "builder" not in read_doc(tmp_path, "agents.toml")["agents"]["minecraft"]
    )  # 旧建筑设计子 Agent 随 v1 玩家停用，整段清理
    for file_name in ("agents.toml", "model.toml"):  # 两文件版本流独立推进到各自链尾
        assert get_config_version(tmp_path, file_name) == tail_version(file_name)
    assert not load_config_dir(tmp_path)[1].missing


@pytest.mark.parametrize(("chars", "expected_tokens"), [(160_000, 200_000), (120_000, 200_000), (300_000, 100_000)])
def test_minecraft_context_chars_become_tokens(tmp_path: Path, chars: int, expected_tokens: int) -> None:
    generate_default_configs(tmp_path)
    doc = _rewound(tmp_path, "agents.toml", "2.0.40")
    context = doc["agents"]["minecraft"]["context"]
    for key in ("window_ratio", "max_tokens", "prepare_ratio", "target_ratio"):
        context.pop(key, None)
    context["max_context_chars"] = chars
    write_doc(tmp_path, "agents.toml", doc)

    load_config_dir(tmp_path)

    migrated = read_doc(tmp_path, "agents.toml")["agents"]["minecraft"]["context"]
    assert (
        "max_context_chars" not in migrated and migrated["max_tokens"] == expected_tokens
    )  # 默认值换新默认，显式值按 3 字符/token 换算

    data = {"agents": {"minecraft": {"context": {"max_context_chars": 600_000, "max_tokens": 50_000}}}}
    assert _upgrade_minecraft_context_tokens(data) == ["agents.minecraft.context.max_context_chars"]
    assert data["agents"]["minecraft"]["context"] == {"max_tokens": 50_000}  # 已有 token 预算不被换算值覆盖
    assert _upgrade_minecraft_context_tokens(data) == []


@pytest.mark.parametrize(
    ("old_cache", "cache_price_in", "expected"),
    [("", 0.2, "hit_discount"), ("", 0.0, "none"), ("anthropic", 0.0, "hit_discount")],
    ids=["empty-with-price", "empty-without-price", "legacy-switch-string"],
)
def test_llm_model_cache_switch_normalized_to_enum(
    tmp_path: Path, old_cache: str, cache_price_in: float, expected: str
) -> None:
    generate_default_configs(tmp_path)
    doc = _rewound(tmp_path, "model.toml", "2.0.42")
    model = doc["llm_models"][0]
    model["cache"], model["cache_price_in"], model["price_in"] = old_cache, cache_price_in, 2.0
    write_doc(tmp_path, "model.toml", doc)

    load_config_dir(tmp_path)

    migrated = read_doc(tmp_path, "model.toml")["llm_models"][0]  # 空串且已填缓存价 = 启用意图明确；价格字段原样保留
    assert (migrated["cache"], migrated["cache_price_in"], migrated["price_in"]) == (expected, cache_price_in, 2.0)
    assert not load_config_dir(tmp_path)[1].has_drift

    with pytest.raises(ValidationError, match="配置矛盾"):  # 声明 none 却填缓存价：分段不生效，加载期硬错
        LLMModelConfig(name="m", cache="none", cache_price_in=0.1)
