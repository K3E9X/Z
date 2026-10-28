# Design reference

- `mockups/*.html`: standalone mockups, open them in a browser. Each uses the theme it was designed in; change `data-theme` (`night`, `day`, `slate`, `contrast`) and `data-font` (`plex`, `inter`, `editorial`, `geist`) on the root element to see the others.
- `screenshots/*.png`: the same screens rendered at their design size.
- `mockups/exports/*.bundle.html`: self-contained exports from the design tool (fonts and scripts inlined), same content as the matching mockup. `Agents-Day.bundle.html` is `Agents.html` in the `day` theme with Geist.
- `tokens.css`: theme and typeface variables. `ui/` maps Tailwind to these variables; the CLI uses the same status colors.

Data in the mockups (names, numbers, model IDs, hosts) is illustrative.

| Screen | Mockup | Default theme |
|---|---|---|
| Mission control | `Main.html` | night |
| Agent at work | `Agent.html` | night |
| New mission | `Launch.html` | day |
| Review | `Inbox.html` | night |
| Tasks | `Tasks.html` | slate |
| Playbooks | `Playbooks.html` | night |
| Agents and skills | `Agents.html` | day |
| Learning | `Learning.html` | slate |
| Connectors | `Connectors.html` | night |
| Inference and usage | `Inference.html` | night |
| History and audit | `History.html` | contrast |
| Command palette | `Palette.html` | night |
| Settings | `Settings.html` | day |
| Mobile | `Mobile.html` | night |
| Command line | `CLI.html` | night |
| Sign in (with error and step-up states) | `SignIn.html` | night |
| Admin | `Admin.html` | slate |
