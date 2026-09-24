-- NULL is unrestricted. An empty array is a scoped manager with no usable groups.
ALTER TABLE user_session ADD COLUMN manager_group_ids TEXT[];
ALTER TABLE console_login_code ADD COLUMN manager_group_ids TEXT[];

-- Old member sessions/codes cannot distinguish a viewer from a manager. Reauthenticate
-- them rather than accidentally grandfathering a manager into unrestricted access.
DELETE FROM user_session AS s USING app_user AS u
WHERE s.user_id = u.id AND u.role = 'member' AND s.method = 'entra';
DELETE FROM console_login_code AS c USING app_user AS u
WHERE c.user_id = u.id AND u.role = 'member';
