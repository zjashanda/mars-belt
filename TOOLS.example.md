# Mars-Belt local config template

# Copy this file to TOOLS.md on the target machine and fill in local secrets.
# TOOLS.md is intentionally ignored by git and must not be published.
#
# For voice playback/TTS validation, also copy:
#   cp deviceInfo_generated.example.json deviceInfo_generated.json
# Then fill local ttsConfig and audioCard values. deviceInfo_generated.json is
# also ignored by git because it contains local credentials and routing.

LISTENAI_TOKEN=

MAIL_FROM_ADDR=
MAIL_PASSWORD=
MAIL_SMTP_SERVER=
MAIL_SMTP_PORT=465
