"""
方块缓存渲染模块
将 BlockCache 中的方块以等距投影渲染为二维图像，便于快速预览。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple, Dict, Set, Any

from PIL import Image, ImageDraw
import colorsys

from src.plugins.maicraft.agent.block_cache.block_cache import BlockCache, CachedBlock, global_block_cache
from src.plugins.maicraft.agent.environment import global_environment


@dataclass
class RenderConfig:
    image_width: int = 1024
    image_height: int = 768
    background_color: Tuple[int, int, int, int] = (16, 16, 20, 255)
    block_size: int = 16  # 单位立方体基准尺寸（像素）
    draw_grid: bool = False
    face_colors: Dict[str, Tuple[int, int, int, int]] = None  # top, left, right
    vertical_scale: float = 1.0  # 垂直高度像素（每上升1格的像素高度 = block_size * vertical_scale）
    type_color_map: Dict[Any, Tuple[int, int, int]] = None  # 可选：类型 -> 基础RGB
    exclude_types: Set[Any] = None  # 需要排除渲染的方块类型（默认排除 air）
    # 轨迹配置
    trail_enabled: bool = True
    trail_max_points: int = 500
    trail_color: Tuple[int, int, int, int] = (255, 210, 0, 180)  # 半透明黄
    trail_width: int = 2
    # 视角对齐（第一人称方向）：将世界绕Y轴按玩家朝向旋转后再做等距投影
    align_to_player_yaw: bool = True

    def __post_init__(self) -> None:
        if self.face_colors is None:
            # 默认为石头风格的中性配色
            self.face_colors = {
                "top": (180, 180, 190, 255),
                "left": (150, 150, 160, 255),
                "right": (120, 120, 130, 255),
            }
        if self.type_color_map is None:
            self.type_color_map = {}
        if self.exclude_types is None:
            self.exclude_types = {"air", 0, "0", None}


class BlockCacheRenderer:
    def __init__(self, cache: Optional[BlockCache] = None, config: Optional[RenderConfig] = None) -> None:
        self.cache = cache or global_block_cache
        self.config = config or RenderConfig()
        # 玩家轨迹（世界坐标，渲染时投影）
        self._player_trail: List[Tuple[float, float, float]] = []

    # === 公共 API ===
    def render(self,
               center: Optional[Tuple[float, float, float]] = None,
               radius: Optional[float] = None,
               save_path: Optional[str] = None) -> Image.Image:
        """
        渲染当前缓存。
        - center+radius 提供则仅渲染该范围内方块。
        - save_path 提供则保存 PNG。
        返回 PIL.Image 对象。
        """
        blocks = self._collect_blocks(center=center, radius=radius)
        img = self._render_blocks(blocks, auto_center=center is None)
        if save_path:
            img.save(save_path, format="PNG")
        return img

    # === 内部方法 ===
    def _collect_blocks(self,
                        center: Optional[Tuple[float, float, float]],
                        radius: Optional[float]) -> List[CachedBlock]:
        if center is not None and radius is not None:
            cx, cy, cz = center
            blocks = self.cache.get_blocks_in_range(cx, cy, cz, radius)
        # 无范围限制则渲染全部
        else:
            blocks = list(self.cache._position_cache.values())  # 受控访问，仅用于渲染预览

        # 过滤掉空气与排除类型
        filtered: List[CachedBlock] = []
        for b in blocks:
            bt = b.block_type
            if bt in self.config.exclude_types:
                continue
            # 常见字符串标记
            if isinstance(bt, str) and bt.lower() == "air":
                continue
            filtered.append(b)
        return filtered

    def _render_blocks(self, blocks: Iterable[CachedBlock], auto_center: bool) -> Image.Image:
        cfg = self.config
        img = Image.new("RGBA", (cfg.image_width, cfg.image_height), cfg.background_color)
        draw = ImageDraw.Draw(img, "RGBA")
        # 保存当前图像用于半透明合成
        self._current_img = img


        # 将世界坐标直接投影到等距坐标（不在世界坐标中做平移），避免小数平移引起的 round 抖动
        projected_raw: List[Tuple[int, int, float, CachedBlock]] = []
        min_px = 10**9
        max_px = -10**9
        min_py = 10**9
        max_py = -10**9
        yaw_rad = self._get_player_yaw()
        for b in blocks:
            x, y, z = b.position.x, b.position.y, b.position.z
            if cfg.align_to_player_yaw and yaw_rad is not None:
                x, z = self._rotate_y(x, z, yaw_rad)
            sx, sy = self._iso_project(x, y, z)
            depth = b.position.x + b.position.y + b.position.z
            projected_raw.append((sx, sy, depth, b))
            if auto_center:
                if sx < min_px:
                    min_px = sx
                if sx > max_px:
                    max_px = sx
                if sy < min_py:
                    min_py = sy
                if sy > max_py:
                    max_py = sy

        # 排序：按深度由小到大（更远先画）
        projected_raw.sort(key=lambda t: t[2])

        # 计算将图像放在画布中心的屏幕平移量
        dx = cfg.image_width // 2
        dy = cfg.image_height // 2
        if auto_center and projected_raw:
            # 优先使用玩家位置居中
            player_pos = getattr(global_environment, "position", None)
            if player_pos is not None:
                px, py = self._iso_project(player_pos.x, player_pos.y, player_pos.z)
                dx -= px
                dy -= py
            else:
                # 回退：使用边界框居中
                center_x = (min_px + max_px) // 2
                center_y = (min_py + max_py) // 2
                dx -= center_x
                dy -= center_y

        for sx, sy, _h, b in projected_raw:
            bt_str = str(b.block_type).lower()
            face_colors = self._get_face_colors_for_type(b.block_type)
            # 叶子类：绿色半透明
            if "leaves" in bt_str or "leave" in bt_str or "leaf" in bt_str:
                green_base = (95, 159, 53)
                face_colors = {
                    "top": self._tone_rgba(green_base, 1.0, 0.55),
                    "left": self._tone_rgba(green_base, 0.85, 0.55),
                    "right": self._tone_rgba(green_base, 0.70, 0.55),
                }

            cxp = sx + dx
            cyp = sy + dy

            # 水体：3/4 高度半透明蓝色
            if "water" in bt_str:
                water_base = (52, 126, 232)
                alpha = 0.60
                water_colors = {
                    "top": self._tone_rgba(water_base, 1.0, alpha),
                    "left": self._tone_rgba(water_base, 0.85, alpha),
                    "right": self._tone_rgba(water_base, 0.70, alpha),
                }
                self._draw_cube(draw, cxp, cyp, cfg.block_size, cfg, water_colors)
            # 草方块特殊顶面
            elif "grass_block" in bt_str:
                self._draw_grass_block_cube(draw, cxp, cyp, cfg.block_size, cfg)
            # 蕨类/矮草用交叉平面
            elif ("fern" in bt_str) or ("shortgrass" in bt_str) or ("short_grass" in bt_str) or ("tall_grass" in bt_str) or ("tallgrass" in bt_str):
                # 使用绿色交叉面片
                green_rgba = (95, 159, 53, 255)
                self._draw_crossed_planes(draw, cxp, cyp, cfg.block_size, cfg, green_rgba)
            else:
                self._draw_cube(draw, cxp, cyp, cfg.block_size, cfg, face_colors)

        # 记录玩家位置并绘制运动轨迹
        try:
            if cfg.trail_enabled:
                ppos = getattr(global_environment, "position", None)
                if ppos is not None:
                    head_world = (ppos.x, ppos.y + 1, ppos.z)
                    if not self._player_trail or self._player_trail[-1] != head_world:
                        self._player_trail.append(head_world)
                        if len(self._player_trail) > cfg.trail_max_points:
                            self._player_trail = self._player_trail[-cfg.trail_max_points:]

                    if len(self._player_trail) >= 2:
                        pts: List[Tuple[int, int]] = []
                        for wx, wy, wz in self._player_trail:
                            rx, rz = (wx, wz)
                            if cfg.align_to_player_yaw and yaw_rad is not None:
                                rx, rz = self._rotate_y(wx, wz, yaw_rad)
                            sx, sy = self._iso_project(rx, wy, rz)
                            pts.append((sx + dx, sy + dy))
                        for i in range(1, len(pts)):
                            self._line(draw, pts[i-1], pts[i], cfg.trail_color, cfg.trail_width)
        except Exception:
            pass

        # 绘制玩家位置箭头（置于顶层）
        try:
            player_pos = getattr(global_environment, "position", None)
            if player_pos is not None:
                # 使用玩家头部位置（y+1）作为箭头基准
                px, py = self._iso_project(player_pos.x, player_pos.y + 1, player_pos.z)
                self._draw_player_marker(draw, px + dx, py + dy, cfg.block_size)
        except Exception:
            pass

        # 清理当前图像引用
        self._current_img = None
        return img

    # 等距投影（菱形格）：
    # 将方块中心 (x,y,z) 投影到 2D。这里采用常见 2:1 isometric。
    def _iso_project(self, x: float, y: float, z: float) -> Tuple[int, int]:
        s = self.config.block_size
        tile_w = s
        tile_h = s // 2
        # 严格 2:1 等距：顶面水平位移基于 tile_w/tile_h，竖直高度使用 s*vertical_scale
        iso_x = round((x - z) * tile_w)
        iso_y = round((x + z) * tile_h - y * (s * self.config.vertical_scale))
        return iso_x, iso_y

    def _draw_cube(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int, cfg: RenderConfig,
                   face_colors: Dict[str, Tuple[int, int, int, int]], height_override: Optional[int] = None) -> None:
        tile_w = s           # 菱形左右偏移
        tile_h = s // 2      # 菱形上下偏移（等距2:1）
        h = height_override if height_override is not None else int(round(s * cfg.vertical_scale))  # 侧面高度像素，需与 _iso_project 的 y 缩放一致
        overlap = 1
        edge = 1

        # 顶面四边形
        top = [
            (cx, cy - tile_h - edge),
            (cx + tile_w + edge, cy - edge),
            (cx, cy + tile_h + overlap),
            (cx - tile_w - edge, cy - edge),
        ]

        # 左侧面
        left = [
            (cx - tile_w - edge, cy - edge),
            (cx, cy + tile_h),
            (cx, cy + tile_h + h + overlap),
            (cx - tile_w - edge, cy + h + overlap - edge),
        ]

        # 右侧面
        right = [
            (cx + tile_w + edge, cy - edge),
            (cx, cy + tile_h),
            (cx, cy + tile_h + h + overlap),
            (cx + tile_w + edge, cy + h + overlap - edge),
        ]

        # 绘制顺序：先左后右最后顶，获得更自然的遮挡
        self._poly(draw, left, face_colors["left"]) 
        self._poly(draw, right, face_colors["right"]) 
        self._poly(draw, top, face_colors["top"]) 

    def _get_face_colors_for_type(self, block_type: Any) -> Dict[str, Tuple[int, int, int, int]]:
        """根据方块类型生成三面颜色。
        优先使用 RenderConfig.type_color_map 中配置；否则基于类型ID生成稳定的 HSV 色调。
        顶面最亮，右侧最暗。
        """
        cfg = self.config
        if block_type in cfg.type_color_map:
            r, g, b = cfg.type_color_map[block_type]
        else:
            bt = str(block_type).lower()
            # 优先匹配常见材料的固定配色
            if "log" in bt:
                # 棕色（木头）
                r, g, b = 121, 85, 58
            elif "dirt" in bt:
                # 淡棕（泥土）
                r, g, b = 160, 120, 80
            elif "andesite" in bt or "andestie" in bt:
                # 安山岩：比石头稍浅的灰
                r, g, b = 150, 150, 150
            elif "stone" in bt:
                # 灰色（石头）
                r, g, b = 130, 130, 130
            else:
                # 稳定色：类型ID映射到 [0,1) 的色相
                hue = (hash(str(block_type)) % 360) / 360.0
                s = 0.25
                v = 0.80
                r_f, g_f, b_f = colorsys.hsv_to_rgb(hue, s, v)
                r, g, b = int(r_f * 255), int(g_f * 255), int(b_f * 255)

        def tone(rgb: Tuple[int, int, int], mul: float) -> Tuple[int, int, int, int]:
            rr = max(0, min(255, int(rgb[0] * mul)))
            gg = max(0, min(255, int(rgb[1] * mul)))
            bb = max(0, min(255, int(rgb[2] * mul)))
            return rr, gg, bb, 255

        return {
            "top": tone((r, g, b), 1.0),
            "left": tone((r, g, b), 0.85),
            "right": tone((r, g, b), 0.70),
        }

    def _apply_alpha(self, face_colors: Dict[str, Tuple[int, int, int, int]], alpha: float) -> Dict[str, Tuple[int, int, int, int]]:
        a = max(0, min(255, int(255 * alpha)))
        return {
            k: (v[0], v[1], v[2], a) for k, v in face_colors.items()
        }

    def _draw_crossed_planes(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int,
                              cfg: RenderConfig, color: Tuple[int, int, int, int]) -> None:
        # 两张互相垂直的立面，与世界 X/Z 轴对齐，并按等距投影呈斜向
        tile_w = s
        tile_h = s // 2
        h = int(round(s * cfg.vertical_scale))

        # 方向向量（等距）
        half_x = (tile_w // 2, tile_h // 2)    # X 轴方向的一半
        half_z = (-tile_w // 2, tile_h // 2)   # Z 轴方向的一半

        # 平面X：顶边端点为 center ± half_x
        x_top_left = (cx - half_x[0], cy - half_x[1])
        x_top_right = (cx + half_x[0], cy + half_x[1])
        x_bot_left = (x_top_left[0], x_top_left[1] + h)
        x_bot_right = (x_top_right[0], x_top_right[1] + h)
        plane_x = [x_top_left, x_top_right, x_bot_right, x_bot_left]

        # 平面Z：顶边端点为 center ± half_z
        z_top_left = (cx - half_z[0], cy - half_z[1])
        z_top_right = (cx + half_z[0], cy + half_z[1])
        z_bot_left = (z_top_left[0], z_top_left[1] + h)
        z_bot_right = (z_top_right[0], z_top_right[1] + h)
        plane_z = [z_top_left, z_top_right, z_bot_right, z_bot_left]

        self._poly(draw, plane_x, color)
        self._poly(draw, plane_z, color)

    def _draw_grass_block_cube(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int,
                               cfg: RenderConfig) -> None:
        tile_w = s
        tile_h = s // 2
        h = int(round(s * cfg.vertical_scale))

        # 基础颜色：顶部草绿，侧面为土的明暗
        grass_top = (95, 159, 53, 255)  # 草绿
        dirt_base = (160, 120, 80)      # 与 dirt 一致

        def tone(rgb: Tuple[int, int, int], mul: float, a: int = 255) -> Tuple[int, int, int, int]:
            rr = max(0, min(255, int(rgb[0] * mul)))
            gg = max(0, min(255, int(rgb[1] * mul)))
            bb = max(0, min(255, int(rgb[2] * mul)))
            return rr, gg, bb, a

        left_color = tone(dirt_base, 0.85)
        right_color = tone(dirt_base, 0.70)

        # 顶面：上边 1/4 绿色，下边 3/4 仍为绿色，但我们用一条水平带体现“上1/4”
        top_poly = [
            (cx, cy - tile_h - 1),
            (cx + tile_w + 1, cy - 1),
            (cx, cy + tile_h + 1),
            (cx - tile_w - 1, cy - 1),
        ]
        self._poly(draw, top_poly, grass_top)

        # 在顶面内画一条稍亮的上边带，近似“上1/4”为绿色高光
        band_height = max(1, tile_h // 4)
        highlight = (110, 180, 70, 255)
        band = [
            (cx, cy - tile_h - 1),
            (cx + tile_w + 1, cy - 1),
            (cx + tile_w, cy - 1 + band_height),
            (cx, cy - tile_h + band_height),
            (cx - tile_w, cy - 1 + band_height),
            (cx - tile_w - 1, cy - 1),
        ]
        self._poly(draw, band, highlight)

        # 左右侧面（基于土色）
        left = [
            (cx - tile_w - 1, cy - 1),
            (cx, cy + tile_h),
            (cx, cy + tile_h + h + 1),
            (cx - tile_w - 1, cy + h),
        ]
        right = [
            (cx + tile_w + 1, cy - 1),
            (cx, cy + tile_h),
            (cx, cy + tile_h + h + 1),
            (cx + tile_w + 1, cy + h),
        ]
        self._poly(draw, left, left_color)
        self._poly(draw, right, right_color)

    def _poly(self, draw: ImageDraw.ImageDraw, points: List[Tuple[int, int]], color: Tuple[int, int, int, int]) -> None:
        """绘制支持半透明合成的多边形。alpha<255 时在临时图层绘制并合成。"""
        if len(color) == 4 and color[3] < 255 and getattr(self, "_current_img", None) is not None:
            w, h = self._current_img.size
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            layer_draw = ImageDraw.Draw(layer, "RGBA")
            layer_draw.polygon(points, fill=color)
            self._current_img.alpha_composite(layer)
        else:
            draw.polygon(points, fill=color)

    def _line(self, draw: ImageDraw.ImageDraw, p1: Tuple[int, int], p2: Tuple[int, int],
              color: Tuple[int, int, int, int], width: int) -> None:
        if len(color) == 4 and color[3] < 255 and getattr(self, "_current_img", None) is not None:
            w, h = self._current_img.size
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            layer_draw = ImageDraw.Draw(layer, "RGBA")
            layer_draw.line([p1, p2], fill=color, width=width)
            self._current_img.alpha_composite(layer)
        else:
            draw.line([p1, p2], fill=color, width=width)

    def _tone_rgba(self, rgb: Tuple[int, int, int], mul: float, alpha: float) -> Tuple[int, int, int, int]:
        rr = max(0, min(255, int(rgb[0] * mul)))
        gg = max(0, min(255, int(rgb[1] * mul)))
        bb = max(0, min(255, int(rgb[2] * mul)))
        aa = max(0, min(255, int(255 * alpha)))
        return rr, gg, bb, aa

    def _draw_player_marker(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, s: int) -> None:
        # 朝上的箭头，亮黄色，带描边
        size = max(10, int(s * 1.2))
        half = size // 2
        arrow = [
            (cx, cy - size),
            (cx + half, cy),
            (cx + half // 2, cy),
            (cx + half // 2, cy + size),
            (cx - half // 2, cy + size),
            (cx - half // 2, cy),
            (cx - half, cy),
        ]
        draw.polygon(arrow, fill=(255, 210, 0, 255))
        draw.line(arrow + [arrow[0]], fill=(20, 20, 20, 255), width=2)

    def _get_player_yaw(self) -> Optional[float]:
        """返回玩家朝向（弧度）。若不可用，返回 None。
        目前数据源未包含 yaw/pitch；后续若加入则从 global_environment 获取。
        暂时可从最近两点轨迹近似推断（不足两点返回 None）。
        """
        try:
            # 预留：如果环境以后提供 yaw，则直接读取
            yaw_deg = getattr(global_environment, "yaw", None)
            if yaw_deg is not None:
                import math
                return math.radians(float(yaw_deg))

            # 从轨迹推断朝向
            if len(getattr(self, "_player_trail", [])) >= 2:
                import math
                (x1, _y1, z1) = self._player_trail[-2]
                (x2, _y2, z2) = self._player_trail[-1]
                dx = x2 - x1
                dz = z2 - z1
                if abs(dx) + abs(dz) > 1e-6:
                    return math.atan2(dx, dz)  # 将 +Z 作为前方
        except Exception:
            pass
        return None

    def _rotate_y(self, x: float, z: float, yaw_rad: float) -> Tuple[float, float]:
        """绕 Y 轴旋转（仅在平面 XZ），使玩家前向对齐屏幕前方。"""
        import math
        cos_y = math.cos(-yaw_rad)
        sin_y = math.sin(-yaw_rad)
        rx = x * cos_y - z * sin_y
        rz = x * sin_y + z * cos_y
        return rx, rz


__all__ = ["BlockCacheRenderer", "RenderConfig"]


