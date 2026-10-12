"""建筑设计用途的模型配置：缺省时沿用已有游戏模型，明确选择的保持不变。"""

from src.modules.config.model_schemas import LLMProfilesConfig


def test_missing_builder_profile_uses_existing_minecraft_models() -> None:
    """用户没有名为 default 的模型时，新增设计用途仍引用已有游戏模型。"""
    profiles = LLMProfilesConfig(minecraft={"model_list": ["custom-game"], "temperature": 0.1})
    assert profiles.minecraft_builder.model_list == ["custom-game"]
    assert profiles.minecraft.temperature == 0.1
    assert profiles.minecraft_builder.temperature != profiles.minecraft.temperature


def test_explicit_builder_profile_is_preserved() -> None:
    """明确选择的设计模型不能被游戏模型的默认继承覆盖。"""
    profiles = LLMProfilesConfig(
        minecraft={"model_list": ["game"]}, minecraft_builder={"model_list": ["designer"], "temperature": 0.9}
    )
    assert profiles.minecraft_builder.model_list == ["designer"]
    assert profiles.minecraft_builder.temperature == 0.9
