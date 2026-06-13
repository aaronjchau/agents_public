# 1Password `op run` template — op:// references only, no secret values.
# Point each ref at your own vault/item (op://<your-vault>/<your-item>/<FIELD>),
# then: op run --env-file=.env.tpl -- <command>. Or skip op entirely and
# export these as plain env vars; shared/settings.py documents each one.
DATABASE_URL=op://<your-vault>/<your-item>/DATABASE_URL
ANTHROPIC_API_KEY=op://<your-vault>/<your-item>/ANTHROPIC_API_KEY
NOTION_TOKEN=op://<your-vault>/<your-item>/NOTION_TOKEN
GMAIL_CLIENT_ID=op://<your-vault>/<your-item>/GMAIL_CLIENT_ID
GMAIL_CLIENT_SECRET=op://<your-vault>/<your-item>/GMAIL_CLIENT_SECRET
GMAIL_REFRESH_TOKEN=op://<your-vault>/<your-item>/GMAIL_REFRESH_TOKEN
ANTHROPIC_ADMIN_API_KEY=op://<your-vault>/<your-item>/ANTHROPIC_ADMIN_API_KEY
LANGSMITH_API_KEY=op://<your-vault>/<your-item>/LANGSMITH_API_KEY
AGENTS_API_TOKEN=op://<your-vault>/<your-item>/AGENTS_API_TOKEN
GMAIL_PUBSUB_TOPIC=op://<your-vault>/<your-item>/GMAIL_PUBSUB_TOPIC
GMAIL_PUBSUB_AUDIENCE=op://<your-vault>/<your-item>/GMAIL_PUBSUB_AUDIENCE
GMAIL_PUBSUB_PUSH_SA=op://<your-vault>/<your-item>/GMAIL_PUBSUB_PUSH_SA
GMAIL_WATCH_EMAIL=op://<your-vault>/<your-item>/GMAIL_WATCH_EMAIL
JOB_APPS_API_URL=op://<your-vault>/<your-item>/JOB_APPS_API_URL
NOTION_JOB_APPS_DB_ID=op://<your-vault>/<your-item>/NOTION_JOB_APPS_DB_ID
NOTION_JOB_APPS_DATA_SOURCE_ID=op://<your-vault>/<your-item>/NOTION_JOB_APPS_DATA_SOURCE_ID
NOTION_COMPANIES_DB_ID=op://<your-vault>/<your-item>/NOTION_COMPANIES_DB_ID
NOTION_COMPANIES_DATA_SOURCE_ID=op://<your-vault>/<your-item>/NOTION_COMPANIES_DATA_SOURCE_ID
MB_TASKS_DATA_SOURCE_ID=op://<your-vault>/<your-item>/MB_TASKS_DATA_SOURCE_ID
MB_FOCUS_HOURS_DATA_SOURCE_ID=op://<your-vault>/<your-item>/MB_FOCUS_HOURS_DATA_SOURCE_ID
MB_LEETCODE_DATA_SOURCE_ID=op://<your-vault>/<your-item>/MB_LEETCODE_DATA_SOURCE_ID
MB_BRIEFS_HUB_DATA_SOURCE_ID=op://<your-vault>/<your-item>/MB_BRIEFS_HUB_DATA_SOURCE_ID
NOTION_NEWS_DATA_SOURCE_ID=op://<your-vault>/<your-item>/NOTION_NEWS_DATA_SOURCE_ID
MB_PRIMARY_CALENDAR_ID=op://<your-vault>/<your-item>/MB_PRIMARY_CALENDAR_ID
MB_PROJECTS=op://<your-vault>/<your-item>/MB_PROJECTS
MB_SCHOOL_PROJECT_IDS=op://<your-vault>/<your-item>/MB_SCHOOL_PROJECT_IDS
