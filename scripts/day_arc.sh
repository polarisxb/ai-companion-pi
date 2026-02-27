#!/bin/bash
# COMPANION DAY ARC
# Shows the emotional trajectory of a day's wakings
# Usage: day_arc.sh [YYYY-MM-DD]  (defaults to today)

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
DATE="${1:-$(date '+%Y-%m-%d')}"

echo "Day Arc — $DATE"
echo "---"

WAKING=1
for f in $(ls "$COMPANION_HOME/journals/wakeup_${DATE}_"*.md 2>/dev/null | sort); do
  FILENAME=$(basename "$f")
  TIME=$(echo "$FILENAME" | sed 's/wakeup_[0-9-]*_\([0-9]*-[0-9]*\)\.md/\1/' | tr '-' ':')
  STATUS=$(grep '^\*\*Status:\*\*' "$f" | sed 's/\*\*Status:\*\* //')
  if [ -n "$STATUS" ]; then
    printf "  %2d. [%s] %s\n" "$WAKING" "$TIME" "$STATUS"
  else
    printf "  %2d. [%s] (no status recorded)\n" "$WAKING" "$TIME"
  fi
  WAKING=$((WAKING + 1))
done

TOTAL=$((WAKING - 1))
if [ "$TOTAL" -eq 0 ]; then
  echo "  No journal entries found for $DATE."
else
  echo "---"
  echo "Total wakings: $TOTAL"
fi
