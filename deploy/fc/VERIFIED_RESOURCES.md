# 已核验的阿里云复用配置

2026-09-06 使用阿里云 API 核验。以下非敏感值已设为 deploy.yml 默认值；同名 Aliyun Environment Variable 仍优先，不覆盖用户配置。

| 参数 | 默认值 |
|---|---|
| FC_ACCOUNT_ID | 1318350152273303 |
| FC_EXECUTION_ROLE | acs:ram::1318350152273303:role/clawworks-staging-fc-vpc-role |
| ALIYUN_OSS_BUCKET | clawworks-server-staging-1318350152273303 |
| CONTENT_ORIGIN | https://zlspace-content.clawworks.cn |
| FC_VPC_ID | vpc-wz9wxvremr3b4sachlnqw |
| FC_VSWITCH_IDS | vsw-wz9sburvm744z729y9krk |
| FC_SECURITY_GROUP_ID | sg-wz96c132ei8aqdpsyiqu |

RDS 实例 rm-wz9ih82ct82ug692x 已创建 gamevallies 独立库，utf8mb4，状态 Running；未创建业务表，未修改其他业务库。专用数据库账号及密码尚待配置。

MySQL 内网：rm-wz9ih82ct82ug692x.mysql.rds.aliyuncs.com:3306。
Redis 内网：r-wz92c2eec71e2ec4.redis.rds.aliyuncs.com:6379，版本 5.0。逻辑库和凭据仍需确认，不能重置共享实例密码。当前锁定 BullMQ 最低 Redis 5.0、建议 6.2；仍需队列实测。

作品域名使用 zlspace-content.clawworks.cn 默认值，尚未绑定 DNS/HTTPS；已有证书不覆盖两个 zlspace 子域名。DNS 由同账号阿里云管理，待函数地址与证书就绪后配置。

Github 连接不具备 Secrets/Variables 管理能力，不能读取或代写密码。LLM_API_KEY/LLM_BASE_URL/LLM_MODEL、DATABASE_URL、REDIS_URL，以及四项独立应用密钥仍由用户填入 Aliyun 环境。FC_FRONTEND_URL 待前端发布后生成。
