# Screen Monitor Plugin

自动屏幕监控插件，实时检测屏幕变化并使用AI分析内容，将分析结果发送到Amaidesu核心系统。

## 功能特性

🔍 **智能屏幕监控**
- 自动截图检测屏幕变化
- 可配置的敏感度和间隔
- 高效的变化检测算法

🤖 **AI内容分析**  
- 使用先进的视觉语言模型
- 支持多张图像拼接分析
- 缓存机制避免丢失变化

📤 **消息发送**
- 自动将AI分析结果发送到核心系统
- 可配置的消息格式和用户信息
- 支持模板和群组功能

## 安装依赖

```bash
pip install openai pillow mss
```

## 配置使用

1. **复制配置文件**
   ```bash
   cp screen_monitor_config-template.toml screen_monitor_config.toml
   ```

2. **修改配置**
   编辑 `screen_monitor_config.toml`：
   - 配置AI模型API密钥和地址
   - 调整截图间隔和敏感度
   - 设置消息发送格式

3. **启用插件**
   在主配置文件中添加：
   ```toml
   [plugins.screen_monitor_plugin]
   enabled = true
   config_file = "src/plugins/read_pingmu/screen_monitor_config.toml"
   ```

## 配置说明

### 基础配置
- `enabled`: 是否启用插件
- `screenshot_interval`: 截图间隔(秒)，建议0.2-1.0
- `diff_threshold`: 变化敏感度，越低越敏感(10-50)

### AI模型配置  
- `api_key`: OpenAI兼容API密钥
- `base_url`: API服务地址
- `model_name`: 视觉模型名称

### 消息配置
- `user_nickname`: 发送者昵称
- `content_format`: 消息内容格式
- `enable_template`: 是否启用模板模式

## 工作流程

1. **屏幕监控**: 定期截图并检测变化
2. **变化检测**: 计算图像差异，超过阈值触发分析  
3. **AI分析**: 调用视觉模型分析屏幕内容
4. **消息发送**: 将分析结果包装成消息发送到核心系统
5. **其他插件处理**: TTS、字幕等插件接收并处理消息

## 输出示例

```
[屏幕描述更新] 用户正在浏览网页，当前显示的是一个技术文档页面
[屏幕变化序列(3帧)] 用户从代码编辑器切换到了浏览器，打开了新的标签页
```

## 性能优化

- **并发控制**: AI处理时缓存新变化，避免积压
- **图像拼接**: 多张连续图像拼接分析，提供完整上下文
- **智能缓存**: 避免丢失重要的屏幕变化信息

## 故障排除

### 常见问题

1. **无法截图**
   - 检查MSS库是否正确安装
   - 确认屏幕访问权限

2. **AI分析失败**  
   - 验证API密钥和网络连接
   - 检查模型名称是否正确

3. **消息未发送**
   - 确认核心系统运行正常
   - 检查消息配置格式

### 调试模式

设置日志级别为DEBUG以获取详细信息：
```python
logging.getLogger('ScreenMonitorPlugin').setLevel(logging.DEBUG)
```

## 统计信息

插件提供详细的运行统计：
- 发送消息数量
- AI分析次数  
- 图像缓存状态
- 拼接分析统计

通过 `get_plugin_status()` 方法获取完整状态信息。 