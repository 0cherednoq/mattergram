# UI capability reference

Generated from `mattergram.ui.capabilities.SERVER_FEATURES`. Server support alone does not prove client support or feature-flag availability.

| Feature | 11.1.0 | 11.7.0 | 11.11.0 | 12.0.2 |
| --- | --- | --- | --- | --- |
| `blocks` | no | no | yes | yes |
| `forms.action_button` | no | no | yes | yes |
| `forms.basic` | yes | yes | yes | yes |
| `forms.checkbox_group` | no | no | no | yes |
| `forms.checkbox_matrix` | no | no | no | yes |
| `forms.collapsible` | no | no | no | yes |
| `forms.date` | yes | yes | yes | yes |
| `forms.date_bounds_config` | no | no | yes | yes |
| `forms.datetime_config` | no | yes | yes | yes |
| `forms.file` | no | no | yes | yes |
| `forms.label_position` | no | no | no | yes |
| `forms.lookup` | yes | yes | yes | yes |
| `forms.manual_time` | no | yes | yes | yes |
| `forms.multi` | yes | yes | yes | yes |
| `forms.refresh` | yes | yes | yes | yes |
| `forms.steps` | yes | yes | yes | yes |
| `forms.timezone` | no | yes | yes | yes |

Use `UIProfile.require(feature)` before serialization. An unknown client declaration remains unknown unless the application supplies explicit evidence or an override for an unknown capability.
