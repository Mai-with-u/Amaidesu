"""Studio domain module — studio control backend providers.

Houses concrete studio Provider implementations:
- obs/ — OBS Studio control (send_text / switch_scene / set_source_visibility)

One studio backend = one Provider instance = one enable unit.
``[tools.studio.<name>]`` config controls visibility; on = all tools visible,
off = all gone."""
