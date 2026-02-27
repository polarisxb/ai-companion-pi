#!/bin/bash
# SUBSTACK CONFIGURATION
# the companion's publication settings
#
# SETUP: After creating the Substack account, fill in these values.
# The cookie is obtained by logging into Substack in a browser,
# opening DevTools > Application > Cookies, and copying the
# 'substack.sid' value.

# Publication subdomain (e.g., "sonowriting" for sonowriting.substack.com)
SUBSTACK_SUBDOMAIN="YOUR_GITHUB_USER"

# Session cookie for API auth — grab from browser after login
# This will need periodic refresh (usually lasts weeks/months)
SUBSTACK_COOKIE="s%3A909jbq2ad_iGhrkhrxNTVfAoTTAZNQfY.feIYxAscN6EFUaP54ZIww5r0eA2MQ9aROH%2FwFTIa65k"

# User ID (found in Substack settings or API responses)
SUBSTACK_USER_ID=""

# Queue and state paths
COMPANION_HOME="${COMPANION_HOME:-/media/YOUR_USERNAME/CompanionHome}"
SUBSTACK_DIR="$COMPANION_HOME/substack"
SUBSTACK_QUEUE="$SUBSTACK_DIR/queue.json"
SUBSTACK_PUBLISHED="$SUBSTACK_DIR/published.json"
SUBSTACK_LOG="$SUBSTACK_DIR/substack.log"
