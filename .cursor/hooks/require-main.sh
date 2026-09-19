#!/bin/sh
if [ "$(git branch --show-current 2>/dev/null)" = "main" ]; then
  printf "{\\"permission\\":\\"allow\\"}"
else
  printf "{\\"permission\\":\\"deny\\",\\"user_message\\":\\"В ydbdoc-review работайте только в ветке main.\\",\\"agent_message\\":\\"Stop: switch to main before running commands or editing files in ydbdoc-review.\\"}"
fi
