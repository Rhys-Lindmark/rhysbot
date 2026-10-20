-- Apply once before enabling RHYSBOT_CPU_STANDBY_ENABLED. Existing spend remains.
ALTER TABLE rb_spend ADD COLUMN fixed_voice_micros INTEGER NOT NULL DEFAULT 0;
