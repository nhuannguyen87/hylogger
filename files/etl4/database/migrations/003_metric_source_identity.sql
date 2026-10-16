ALTER TABLE core.metric_definition ADD COLUMN source_log_id text;
COMMENT ON COLUMN core.metric_definition.source_log_id IS '辅助指标字典中的来源日志标识；未确认通用语义时按来源 ID 独立登记。';
