CREATE TABLE `agent_access_address` (
  `id` bigint(20) NOT NULL COMMENT 'id',
  `access_address` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '访问地址',
  `business_id` bigint(20) NOT NULL COMMENT '业务id',
  `type` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '类型 1：agent使用 2：会话管理使用',
  `day_max_message` int(5) DEFAULT NULL COMMENT '每日最大消息数',
  `status` char(1) COLLATE utf8mb4_unicode_ci DEFAULT '1' COMMENT '启用状态 1启用 0停用',
  `remark` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '备注',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户id',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='访问地址管理';

CREATE TABLE `agent_favorite` (
  `id` bigint(20) NOT NULL COMMENT '主键',
  `agent_id` bigint(20) NOT NULL COMMENT 'agent_id',
  `user_id` bigint(20) NOT NULL COMMENT '用户id',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户id',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='agent权限表';

CREATE TABLE `agent_info` (
  `id` bigint(20) NOT NULL COMMENT '唯一标识',
  `agent_name` varchar(256) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `abbreviation` varchar(260) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `agent_describe` varchar(400) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '简要说明',
  `dify_ip` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'dify服务器地址',
  `agent_key` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '对应agentkey',
  `is_new` bit(1) DEFAULT b'1' COMMENT '是否new标识（1-是，0-否）',
  `sort` int(4) DEFAULT NULL COMMENT '排序',
  `agent_enable` bit(1) DEFAULT b'1' COMMENT '禁启用（1-启用，0-禁用）',
  `agent_introduce` mediumtext COLLATE utf8mb4_unicode_ci,
  `prologue` mediumtext COLLATE utf8mb4_unicode_ci,
  `reply_content` varchar(400) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '定时回复内容',
  `reply_interval` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '定时回复时间',
  `third_party` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否第三方链接',
  `icon_yes` bigint(20) DEFAULT NULL COMMENT '选中图标',
  `icon_no` bigint(20) DEFAULT NULL COMMENT '非选中图标',
  `open_digit_people` bit(1) DEFAULT NULL COMMENT '是否开启数字人',
  `digit_people_id` bigint(20) DEFAULT NULL COMMENT '数字人id',
  `remark` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '备注',
  `carry_user_info` tinyint(1) DEFAULT NULL COMMENT '是否携带用户信息',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `maintainer` bigint(20) DEFAULT NULL COMMENT '维护人',
  `origin` tinyint(4) DEFAULT NULL COMMENT '来源，1-添加，2-同步',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户ID',
  `app_id` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Dify中AppId',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='agent管理-agent信息表';

CREATE TABLE `agent_permission` (
  `id` bigint(20) NOT NULL,
  `agent_id` bigint(20) NOT NULL,
  `open_to_all` bit(1) DEFAULT NULL COMMENT '1:开放所有人，0:指定范围',
  `staff_allowed` enum('DENY','ALL','SPECIFIC') DEFAULT NULL COMMENT '教职工权限，如："ALL"、"SPECIFIC"、"DENY"',
  `student_allowed` enum('DENY','ALL','SPECIFIC') DEFAULT NULL COMMENT '学生权限，如："ALL"、"SPECIFIC"、"DENY"',
  `staff_scope` json DEFAULT NULL COMMENT '教职工权限范围，如：{"department": ["计算机学院"]}',
  `student_scope` json DEFAULT NULL COMMENT '学生权限范围，如：{"major": ["护理专业"], "grade": ["2025级"]}',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户id',
  PRIMARY KEY (`id`),
  KEY `idx_agent_id` (`agent_id`),
  KEY `idx_open_to_all` (`open_to_all`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='agent权限表';

CREATE TABLE `agent_statistical_analysis_config` (
  `id` bigint(20) NOT NULL COMMENT '唯一标识',
  `analysis_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '统计分析名称',
  `terminal_type` tinyint(4) NOT NULL COMMENT '使用终端类型(1-PC,2-移动)',
  `agent_id` bigint(20) DEFAULT NULL COMMENT '关联智能体ID',
  `chart_url` varchar(1024) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '图表访问地址',
  `remarks` text COLLATE utf8mb4_unicode_ci COMMENT '备注',
  `maintainer` bigint(20) DEFAULT NULL COMMENT '维护人',
  `sort_order` int(11) DEFAULT NULL COMMENT '排序',
  `dashboard_intro` varchar(1024) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '看板介绍',
  `allowed` enum('ONESELF','ALL','SPECIFIC') COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '人员权限设置',
  `cover_image` bigint(20) DEFAULT NULL COMMENT '封面图 附件id',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户编号',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='统计分析配置表';

CREATE TABLE `agent_statistical_analysis_permission` (
  `id` bigint(20) NOT NULL COMMENT '唯一标识',
  `config_id` bigint(20) NOT NULL COMMENT '统计分析配置ID',
  `open_to_all` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否开放给所有人 1:开放所有人，0:指定范围',
  `staff_allowed` varchar(20) DEFAULT NULL COMMENT '教职工权限，如："ALL"、"SPECIFIC"、"DENY"',
  `student_allowed` varchar(20) DEFAULT NULL COMMENT '学生权限，如："ALL"、"SPECIFIC"、"DENY"',
  `staff_scope` json DEFAULT NULL COMMENT '教职工权限范围，如：{"tags": ["11111"], "groups": ["1111111"]}',
  `student_scope` json DEFAULT NULL COMMENT '学生权限范围，如：{"major": ["护理专业"], "grade": ["2025级"]}',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户编号',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  PRIMARY KEY (`id`),
  KEY `idx_config_id` (`config_id`),
  KEY `idx_open_to_all` (`open_to_all`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='统计分析配置权限表';

CREATE TABLE `b_limit_setting` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `type` int(2) NOT NULL COMMENT '设置类型 1：全局设置 2：按角色设置 3：按智能体设置',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '删除标志（0代表存在 1代表删除）',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户id',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=2003664711783124994 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='额度设置信息表';

CREATE TABLE `b_limit_setting_detail` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `type` int(2) NOT NULL COMMENT '设置类型 1：全局设置 2：按角色设置 3：按智能体设置',
  `data_id` bigint(20) DEFAULT NULL COMMENT '数据id 角色id或者智能体id',
  `day_max_message` int(5) DEFAULT NULL COMMENT '用户每日最大消息数',
  `all_max_message` int(5) DEFAULT NULL COMMENT '用户最多消息数',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '删除标志（0代表存在 1代表删除）',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户id',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=2020773983680512002 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='额度设置明细信息表';

CREATE TABLE `b_staff_basic` (
  `id` bigint(20) NOT NULL COMMENT '唯一标识',
  `name` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '姓名',
  `mobile` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '手机号',
  `code` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '教职工号',
  `school_name` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '学校名称',
  `tags` text CHARACTER SET utf8 COMMENT '标签（多个标签以分号;分隔）',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户id',
  PRIMARY KEY (`id`) USING BTREE,
  KEY `name` (`name`,`mobile`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='教职工信息表';

CREATE TABLE `b_staff_group` (
  `id` bigint(20) NOT NULL COMMENT '唯一标识',
  `group_name` varchar(50) NOT NULL COMMENT '分组名称',
  `creator_id` bigint(20) NOT NULL COMMENT '创建人ID',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户编号',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8 ROW_FORMAT=DYNAMIC COMMENT='个人分组规则表';

CREATE TABLE `b_staff_group_member` (
  `id` bigint(20) NOT NULL COMMENT '唯一标识',
  `group_id` bigint(20) NOT NULL COMMENT '分组ID',
  `staff_id` bigint(20) NOT NULL COMMENT '教职工ID',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户编号',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8 ROW_FORMAT=DYNAMIC COMMENT='个人分组规则成员表';

CREATE TABLE `b_staff_tag` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '唯一标识',
  `tag_name` varchar(50) NOT NULL COMMENT '标签名称',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户编号',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=2042544187633246211 DEFAULT CHARSET=utf8 ROW_FORMAT=DYNAMIC COMMENT='教职工标签表';

CREATE TABLE `b_student_info` (
  `id` bigint(20) NOT NULL COMMENT '唯一标识',
  `name` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '姓名',
  `mobile` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '手机号',
  `code` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '学号',
  `school_name` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '学校名称',
  `grade_name` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '年级',
  `major_name` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '专业',
  `class_name` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '班级',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户id',
  PRIMARY KEY (`id`) USING BTREE,
  KEY `name` (`name`,`mobile`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='学生信息表';

CREATE TABLE `b_user_session_record` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `user_id` bigint(20) NOT NULL COMMENT '使用人员id',
  `agent_id` bigint(20) NOT NULL COMMENT '使用智能体id',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '删除标志（0代表存在 1代表删除）',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=2056305144818909186 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='用户会话记录信息表';

CREATE TABLE `digit_use_record` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT 'ID',
  `code` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '访问地址编码',
  `business_id` bigint(20) NOT NULL COMMENT '业务id',
  `type` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '类型 1：agent使用 2：会话管理使用',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '删除标志（0代表存在 1代表删除）',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=2053756680876670978 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='数字人使用记录信息表';

CREATE TABLE `sys_role` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '角色ID',
  `name` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '角色名称',
  `code` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '角色标识',
  `sort` int(4) NOT NULL COMMENT '显示顺序',
  `status` char(1) COLLATE utf8mb4_unicode_ci DEFAULT '0' COMMENT '角色状态（0正常 1停用）',
  `type_name` varchar(45) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '角色类型名称(教职工、学生、系统、其他)',
  `type_code` varchar(45) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '角色类型编码',
  `data_scope` char(1) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '数据范围（1：全部数据权限 2：自定数据权限 3：本部门数据权限 4：本部门及以下数据权限）',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '删除标志（0代表存在 2代表删除）',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `remark` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '备注',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户id',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=2052935911972093954 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色信息表';

CREATE TABLE `sys_role_menu` (
  `id` bigint(20) NOT NULL COMMENT '唯一标识',
  `role_id` bigint(20) NOT NULL COMMENT '角色ID',
  `menu_id` bigint(20) NOT NULL COMMENT '菜单ID',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) NOT NULL DEFAULT '0' COMMENT '租户编号',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色和菜单关联表';

CREATE TABLE `sys_user` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '用户ID',
  `user_name` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '用户账号',
  `password` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '密码',
  `nick_name` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '用户昵称',
  `remark` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '备注',
  `dept_id` bigint(20) DEFAULT NULL COMMENT '部门ID',
  `post_ids` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '岗位编号数组',
  `email` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT '' COMMENT '用户邮箱',
  `mobile` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT '' COMMENT '手机号码',
  `sex` tinyint(4) DEFAULT '0' COMMENT '用户性别',
  `avatar` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT '' COMMENT '头像地址',
  `user_type` tinyint(4) DEFAULT '1' COMMENT '用户类型',
  `status` tinyint(4) NOT NULL DEFAULT '0' COMMENT '帐号状态（0正常 1停用）',
  `login_ip` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT '' COMMENT '最后登录IP',
  `login_date` datetime DEFAULT NULL COMMENT '最后登录时间',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户id',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE KEY `idx_username` (`id`,`user_name`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=2052987666302844931 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户信息表';

CREATE TABLE `sys_user_role` (
  `id` bigint(20) NOT NULL COMMENT '唯一标识',
  `user_id` bigint(20) NOT NULL COMMENT '用户ID',
  `role_id` bigint(20) NOT NULL COMMENT '角色ID',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  `tenant_id` bigint(20) DEFAULT NULL COMMENT '租户id',
  PRIMARY KEY (`id`) USING BTREE,
  KEY `idx_user_role_id` (`user_id`,`role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户和角色关联表';

CREATE TABLE `system_menu` (
  `id` bigint(20) NOT NULL COMMENT '菜单ID',
  `name` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '菜单名称',
  `permission` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '权限标识',
  `type` tinyint(4) NOT NULL COMMENT '菜单类型',
  `sort` int(11) NOT NULL DEFAULT '0' COMMENT '显示顺序',
  `parent_id` bigint(20) NOT NULL DEFAULT '0' COMMENT '父菜单ID',
  `path` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT '' COMMENT '路由地址',
  `icon` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT '#' COMMENT '菜单图标',
  `component` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '组件路径',
  `component_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '组件名',
  `status` tinyint(4) NOT NULL DEFAULT '0' COMMENT '菜单状态',
  `visible` bit(1) NOT NULL DEFAULT b'1' COMMENT '是否可见',
  `keep_alive` bit(1) NOT NULL DEFAULT b'1' COMMENT '是否缓存',
  `always_show` bit(1) NOT NULL DEFAULT b'1' COMMENT '是否总是显示',
  `full_id_path` varchar(1024) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '全id路径',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='菜单权限表';

CREATE TABLE `system_tenant` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '租户编号',
  `name` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '租户名',
  `code` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '租户代号',
  `contact_name` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '联系人',
  `contact_mobile` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '联系手机',
  `status` tinyint(4) NOT NULL DEFAULT '0' COMMENT '租户状态（1正常 0停用）',
  `package_id` bigint(20) NOT NULL COMMENT '租户套餐编号',
  `remark` varchar(60) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '备注',
  `sort` int(4) DEFAULT NULL COMMENT '排序',
  `disable_prompts` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '停用提示语',
  `step_code` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '步骤完成code 1,2,3。1：租户创建 2：配置地址 3：配置智能体 4：配置管理员',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=2052935911720435714 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='租户表';

CREATE TABLE `system_tenant_package` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '套餐编号',
  `name` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '套餐名',
  `remark` varchar(256) COLLATE utf8mb4_unicode_ci DEFAULT '' COMMENT '备注',
  `menu_ids` json NOT NULL COMMENT '关联的菜单编号',
  `created_by` bigint(20) DEFAULT NULL COMMENT '创建者',
  `created_time` datetime DEFAULT NULL COMMENT '创建时间',
  `updated_by` bigint(20) DEFAULT NULL COMMENT '更新者',
  `updated_time` datetime DEFAULT NULL COMMENT '更新时间',
  `deleted` bit(1) NOT NULL DEFAULT b'0' COMMENT '是否删除',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=2008064582484865027 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC COMMENT='租户套餐表';