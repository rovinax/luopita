"""Warm/cold group memory helpers mixed into Database implementations."""

from __future__ import annotations

# SQL fragments for Postgres warm tables (group shards / summaries / profiles).
GROUP_CONTEXT_SQL = """
CREATE TABLE IF NOT EXISTS group_shards (
    shard_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    centroid JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_group_shards_user
    ON group_shards(platform, chat_id, user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS shard_summaries (
    shard_id TEXT PRIMARY KEY REFERENCES group_shards(shard_id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS user_profiles (
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    preferences TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (platform, user_id)
);
CREATE TABLE IF NOT EXISTS shard_turns (
    id BIGSERIAL PRIMARY KEY,
    shard_id TEXT NOT NULL REFERENCES group_shards(shard_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_shard_turns_shard ON shard_turns(shard_id, id);
"""
