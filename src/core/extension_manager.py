"""
ExtensionManager - 扩展管理器

管理所有Extension的生命周期，包括：
- 自动扫描src/extensions/和extensions/目录
- 按依赖顺序加载Extension
- 支持启用/禁用配置
- Extension生命周期管理（加载、卸载、查询）
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

if TYPE_CHECKING:
    from .event_bus import EventBus
    from .extension import Extension, ExtensionInfo

from src.utils.logger import get_logger


class ExtensionLoadError(Exception):
    """Extension加载失败异常"""

    pass


class ExtensionDependencyError(Exception):
    """Extension依赖错误异常"""

    pass


class ExtensionManager:
    """
    Extension管理器

    职责：
    1. 扫描和发现Extension（src/extensions/和extensions/）
    2. 按依赖顺序加载Extension
    3. 管理Extension生命周期（setup、cleanup）
    4. 提供Extension查询和卸载功能
    """

    def __init__(self, event_bus: "EventBus"):
        """
        初始化ExtensionManager

        Args:
            event_bus: 事件总线实例
        """
        self._event_bus = event_bus
        self._extensions: Dict[str, "Extension"] = {}
        self._extension_infos: Dict[str, "ExtensionInfo"] = {}
        self._loaded_paths: Set[str] = set()
        self.logger = get_logger("ExtensionManager")
        self.logger.info("ExtensionManager初始化完成")

    async def load_all_extensions(
        self, extensions_config: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> Dict[str, bool]:
        """
        加载所有Extension

        加载流程：
        1. 扫描src/extensions/和extensions/目录
        2. 收集所有Extension类
        3. 解析依赖关系
        4. 按拓扑顺序加载Extension
        5. 调用每个Extension的setup()方法

        Args:
            extensions_config: 扩展配置字典 {extension_name: config_dict}

        Returns:
            Dict[str, bool]: 加载结果 {extension_name: success}

        Raises:
            ExtensionDependencyError: 依赖解析失败
        """
        self.logger.info("开始加载所有Extension...")

        extensions_config = extensions_config or {}
        results = {}

        # 1. 扫描并收集所有Extension
        discovered_extensions = self._discover_extensions()

        if not discovered_extensions:
            self.logger.warning("未发现任何Extension")
            return results

        self.logger.info(f"发现 {len(discovered_extensions)} 个Extension")

        # 2. 收集Extension信息和依赖
        extension_graph: Dict[str, List[str]] = {}
        extension_classes: Dict[str, type] = {}

        for ext_name, ext_class in discovered_extensions.items():
            # 检查是否启用
            ext_config = extensions_config.get(ext_name, {})
            if not ext_config.get("enabled", True):
                self.logger.info(f"Extension已禁用，跳过: {ext_name}")
                continue

            # 创建临时实例获取信息
            try:
                temp_instance = ext_class(ext_config)
                info = temp_instance.get_info()
                dependencies = temp_instance.get_dependencies()

                self._extension_infos[ext_name] = info
                extension_graph[ext_name] = dependencies
                extension_classes[ext_name] = ext_class

                self.logger.debug(f"Extension: {ext_name}, 依赖: {dependencies}, 版本: {info.version}")
            except Exception as e:
                self.logger.error(f"获取Extension信息失败 {ext_name}: {e}")
                results[ext_name] = False
                continue

        # 3. 检测循环依赖
        if self._has_circular_dependency(extension_graph):
            self.logger.error("检测到循环依赖，无法加载Extension")
            raise ExtensionDependencyError("Extension存在循环依赖")

        # 4. 拓扑排序确定加载顺序
        load_order = self._topological_sort(extension_graph)

        self.logger.info(f"Extension加载顺序: {' -> '.join(load_order)}")

        # 5. 按顺序加载Extension
        for ext_name in load_order:
            try:
                ext_class = extension_classes[ext_name]
                ext_config = extensions_config.get(ext_name, {})

                # 创建Extension实例
                extension = ext_class(ext_config)

                # 调用setup()
                providers = await extension.setup(self._event_bus, ext_config)

                # 保存Extension
                self._extensions[ext_name] = extension
                results[ext_name] = True

                self.logger.info(f"Extension加载成功: {ext_name} (管理 {len(providers)} 个Provider)")

            except Exception as e:
                self.logger.error(f"Extension加载失败 {ext_name}: {e}", exc_info=True)
                results[ext_name] = False

        # 6. 汇总结果
        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)

        self.logger.info(f"Extension加载完成: {success_count}/{total_count} 成功, {total_count - success_count} 失败")

        return results

    async def get_extension(self, name: str) -> Optional["Extension"]:
        """
        获取Extension实例

        Args:
            name: Extension名称

        Returns:
            Extension实例，如果不存在则返回None
        """
        return self._extensions.get(name)

    async def unload_extension(self, name: str) -> bool:
        """
        卸载Extension

        卸载流程：
        1. 检查Extension是否存在
        2. 检查是否有其他Extension依赖此Extension
        3. 调用Extension的cleanup()方法
        4. 从_extensions中移除

        Args:
            name: Extension名称

        Returns:
            bool: 是否卸载成功

        Raises:
            ExtensionDependencyError: 有其他Extension依赖此Extension
        """
        extension = self._extensions.get(name)

        if extension is None:
            self.logger.warning(f"Extension不存在，无法卸载: {name}")
            return False

        # 检查依赖
        dependents = self._get_dependents(name)
        if dependents:
            self.logger.warning(f"无法卸载 {name}，以下Extension依赖它: {', '.join(dependents)}")
            raise ExtensionDependencyError(f"存在依赖Extension: {', '.join(dependents)}")

        try:
            # 调用cleanup()
            await extension.cleanup()

            # 从_extensions中移除
            del self._extensions[name]

            self.logger.info(f"Extension卸载成功: {name}")
            return True

        except Exception as e:
            self.logger.error(f"Extension卸载失败 {name}: {e}", exc_info=True)
            return False

    def get_loaded_extensions(self) -> List[str]:
        """
        获取所有已加载的Extension名称

        Returns:
            List[str]: Extension名称列表
        """
        return list(self._extensions.keys())

    def get_extension_info(self, name: str) -> Optional["ExtensionInfo"]:
        """
        获取Extension信息

        Args:
            name: Extension名称

        Returns:
            ExtensionInfo对象，如果不存在则返回None
        """
        return self._extension_infos.get(name)

    def get_all_extension_infos(self) -> Dict[str, "ExtensionInfo"]:
        """
        获取所有Extension信息

        Returns:
            Dict[str, ExtensionInfo]: Extension信息字典
        """
        return self._extension_infos.copy()

    async def reload_extension(self, name: str) -> bool:
        """
        重新加载Extension

        先卸载再加载Extension

        Args:
            name: Extension名称

        Returns:
            bool: 是否重新加载成功
        """
        self.logger.info(f"重新加载Extension: {name}")

        # 获取Extension配置
        extension = self._extensions.get(name)
        if extension is None:
            self.logger.warning(f"Extension不存在，无法重新加载: {name}")
            return False

        # 使用getattr安全地访问config属性（Extension协议可能不定义它）
        config = getattr(extension, "config", {})
        if not config:
            self.logger.warning(f"Extension没有config属性，使用空配置: {name}")
            config = {}

        # 卸载
        try:
            await extension.cleanup()
        except Exception as e:
            self.logger.error(f"卸载Extension失败 {name}: {e}")
            return False

        # 重新创建并加载
        try:
            ext_class = extension.__class__
            new_extension = ext_class(config)

            await new_extension.setup(self._event_bus, config)

            # 更新_extensions
            self._extensions[name] = new_extension

            self.logger.info(f"Extension重新加载成功: {name}")
            return True

        except Exception as e:
            self.logger.error(f"重新加载Extension失败 {name}: {e}", exc_info=True)
            return False

    async def cleanup_all(self) -> None:
        """
        清理所有Extension

        按加载顺序的逆序清理Extension（确保依赖关系正确处理）
        """
        self.logger.info("开始清理所有Extension...")

        # 获取加载顺序的逆序
        load_order = list(self._extensions.keys())

        for ext_name in reversed(load_order):
            extension = self._extensions.get(ext_name)
            if extension:
                try:
                    await extension.cleanup()
                    self.logger.debug(f"Extension清理完成: {ext_name}")
                except Exception as e:
                    self.logger.error(f"Extension清理失败 {ext_name}: {e}", exc_info=True)

        self._extensions.clear()
        self._extension_infos.clear()
        self._loaded_paths.clear()

        self.logger.info("所有Extension清理完成")

    def _discover_extensions(self) -> Dict[str, type]:
        """
        扫描src/extensions/和extensions/目录，发现所有Extension

        Returns:
            Dict[str, type]: Extension类字典 {extension_name: extension_class}
        """
        discovered: Dict[str, type] = {}

        # 扫描目录列表
        scan_dirs = [
            Path("src/extensions"),
            Path("extensions"),
        ]

        for scan_dir in scan_dirs:
            if not scan_dir.exists():
                self.logger.debug(f"目录不存在，跳过: {scan_dir}")
                continue

            self.logger.info(f"扫描目录: {scan_dir}")

            for ext_dir in scan_dir.iterdir():
                # 跳过非目录和以_开头的目录
                if not ext_dir.is_dir() or ext_dir.name.startswith("_"):
                    continue

                # 检查extension.py或main.py文件
                ext_files = [
                    ext_dir / "extension.py",
                    ext_dir / "main.py",
                ]

                ext_file = None
                for f in ext_files:
                    if f.exists():
                        ext_file = f
                        break

                if ext_file is None:
                    self.logger.debug(f"未找到extension.py或main.py，跳过: {ext_dir}")
                    continue

                # 导入Extension
                try:
                    # 动态导入模块
                    module_path = f"{scan_dir.name}.{ext_dir.name}.{ext_file.stem}"

                    if module_path not in sys.modules:
                        spec = importlib.util.spec_from_file_location(module_path, ext_file)
                        if spec and spec.loader:
                            module = importlib.util.module_from_spec(spec)
                            sys.modules[module_path] = module
                            spec.loader.exec_module(module)
                        else:
                            continue

                    module = sys.modules[module_path]

                    # 查找Extension类（继承BaseExtension或实现Extension协议）
                    from .extension import BaseExtension

                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)

                        # 跳过非类或已知的内置类
                        if not isinstance(attr, type):
                            continue

                        # 检查是否是Extension（BaseExtension的子类）
                        if issubclass(attr, BaseExtension) and attr is not BaseExtension:
                            ext_name = ext_dir.name
                            if ext_name not in discovered:
                                discovered[ext_name] = attr
                                self.logger.debug(f"发现Extension: {ext_name} -> {attr}")
                            break

                except Exception as e:
                    self.logger.error(f"导入Extension失败 {ext_dir}: {e}", exc_info=True)
                    continue

        return discovered

    def _has_circular_dependency(self, graph: Dict[str, List[str]]) -> bool:
        """
        检测循环依赖

        Args:
            graph: 依赖图 {extension_name: [dependencies]}

        Returns:
            bool: 是否存在循环依赖
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        colors = {node: WHITE for node in graph}

        def dfs(node: str) -> bool:
            if colors[node] == GRAY:
                return True  # 找到环
            if colors[node] == BLACK:
                return False  # 已处理

            colors[node] = GRAY

            for dep in graph.get(node, []):
                if dep in graph:  # 只检查在图中的节点
                    if dfs(dep):
                        return True

            colors[node] = BLACK
            return False

        for node in graph:
            if colors[node] == WHITE:
                if dfs(node):
                    return True

        return False

    def _topological_sort(self, graph: Dict[str, List[str]]) -> List[str]:
        """
        拓扑排序（Kahn算法）

        Args:
            graph: 依赖图 {extension_name: [dependencies]}

        Returns:
            List[str]: 拓扑排序结果
        """
        # 计算入度
        in_degree: Dict[str, int] = {node: 0 for node in graph}

        for node in graph:
            for dep in graph[node]:
                if dep in graph:  # 只计算在图中的节点
                    in_degree[node] += 1

        # 找到入度为0的节点
        queue: List[str] = [node for node, degree in in_degree.items() if degree == 0]
        result: List[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            # 减少依赖此节点的节点的入度
            for other_node in graph:
                if node in graph[other_node]:
                    in_degree[other_node] -= 1
                    if in_degree[other_node] == 0:
                        queue.append(other_node)

        # 检查是否所有节点都已处理（如果有环，结果会少于图的大小）
        if len(result) != len(graph):
            raise ExtensionDependencyError("存在循环依赖，无法进行拓扑排序")

        return result

    def _get_dependents(self, extension_name: str) -> List[str]:
        """
        获取依赖指定Extension的所有Extension

        Args:
            extension_name: Extension名称

        Returns:
            List[str]: 依赖此Extension的Extension列表
        """
        dependents: List[str] = []

        for ext_name, info in self._extension_infos.items():
            if extension_name in info.dependencies:
                dependents.append(ext_name)

        return dependents
