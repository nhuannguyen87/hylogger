-- The prepared dictionary uses NULL when probability semantics are not applicable
-- or not confirmed. A missing declaration must not be coerced into false.
ALTER TABLE core.metric_definition ALTER COLUMN is_probability DROP NOT NULL;
COMMENT ON COLUMN core.metric_definition.is_probability IS '来源/规范字典的概率语义：true/false/未知或不适用的 NULL；Wt 明确为 false。';
