# 航标灯光巡检台

登记航标实测光强和方位偏差，服务端当场给出合格或不合格。页面由 Django 模板直接渲染，浏览器不另跑一套单页应用。

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3190 |
| 应用 | http://localhost:8190 |
| PostgreSQL | localhost:54390 |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| keeper | light123456 | 可登记 |
| watch | watch123456 | 只读 |

## 启动

```bash
cd projects/11-nav-aid-inspection
docker compose up --build
```

## 验收

1. 打开 http://localhost:3190 ，用 keeper 登录，列表里 LH-01 合格、LH-09 不合格。
2. 再登记一条光强低于要求的记录，结论为光强不足。
3. 换 watch 登录，没有登记入口；直接提交登记会拒绝。
