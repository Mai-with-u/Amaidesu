"""Avatar domain module — virtual avatar backend providers.

Houses concrete avatar Provider implementations:
- vts/ — VTubeStudio control (includes VRChat OSC bridge sub-provider)
- warudo/ — Warudo control

One avatar backend = one Provider instance = one enable unit.
``[tools.avatar.<name>]`` config controls visibility; on = all tools visible,
off = all gone."""
