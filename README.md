# 摩尔勇士宠物洗点 GUI

基于 `ref/moer.py` 中 `xd` 洗宠流程实现的桌面界面。界面会读取背包宠物、仓库宠物和五类洗点道具，支持基础五项或战斗面板两种目标判断，并逐次展示洗点结果。仓库初始列表只读取昵称、种类和等级，选中具体宠物时才通过 `_get_pet_info` 获取详细资料。

宠物详情区提供“修改昵称”按钮，可修改背包或仓库宠物昵称，并通过 `_pet_change_name` 调用 1552 协议。技能区支持多选学习预设宠物技能；学习前会通过 1004 协议进入皮克司研究所并等待服务端确认，再通过 1601 协议逐个学习和校验回包。背包列表还提供“放回仓库”按钮，仓库列表提供“取回背包”和“放生宠物”按钮，分别调用 `_pet_back_store`、`_pet_back_bag` 和 1561 协议。放生前会弹窗二次确认，确认后无法恢复；仓库宠物需要取回背包后才能洗点。

“成长预测”页会结合所选宠物的当前等级、转生次数、已分配点总数、五项努力值和战斗面板，按本地 V15 公式和手动自由加点进行匹配。0转99级和10转99级预测会按当前五项自由加点的分布比例放大到98点，不再固定全力或全智；当前尚未分配自由点时才按力量/智力较高项作为默认兜底。大丸子与开蛋统一使用原始五项各取整数 -5～+5、再按目标成长双向均摊的候选规则，支持中心成长 -25～中心成长。程序优先返回五项努力值与五项战斗值全部完全一致的候选；仅在没有精确候选时，才采用网页同款显示容差（生命、攻击、防御、速度允许相差 5，魔力允许相差 1），并在界面明确标注为容差结果。符合条件的候选会用于推算 0转1级、0转99级和10转99级；若存在多解，界面按网页权重排序并列出全部候选的基数、相对基础基数浮动、当前加点和三档完整面板。忘记自由加点分配时，可点击“未知加点预测”：工具会临时重置宠物自由点，以重置回包计算预测，再按重置前后的五项努力值差恢复原加点；恢复完成前会阻止关闭窗口。

“基数与洗宠模拟”页会按当前服务器和宠物种类读取 `ref/pet_bases.json` 中的原始基数。手动输入五项洗宠波动后得到目标基数，并可分别配置 0转99级、0转任意级别和10转99级的自由加点，最终同时计算四种场景的五项及完整战斗面板。

“杂项”页会分别显示背包和仓库中的巨石碎片、经验书数量。可按输入数量将巨石碎片兑换为大丸子，或批量使用经验书；“一键升级10转99级”会按每本99999经验计算经验树缺口，自动使用所需经验书后为当前宠物分配经验并转生。还可读取经验树剩余经验，向当前背包宠物分配指定经验（不转生）、升级到0转99级，或一键分配经验树的全部剩余经验并在达标后自动转生。宠物达到10转99级后的多余经验会保留在经验树中。分配经验后会通过1603协议自动分配属性点，并刷新宠物和经验树数据。开蛋下拉框包含 `ref/item_names.py` 中的全部精灵蛋和精品蛋，开启时优先使用背包库存，不足部分从仓库取出，再通过1130协议批量开启。地图传送独立成行，登录时显示1006协议返回的当前地图和坐标，并在1004协议确认传送成功后同步更新。

杂项页还支持配置 PushPlus Token。保存后会在下次启动继续使用；Token 留空时关闭消息提示。成功和失败通知均包含宠物昵称、宠物类型、洗宠次数、当前选择的道具及剩余数量，以及本次洗宠的开始和停止时间。成功消息标题为“洗宠成功”，结果标记为已达到预期目标，并额外包含当前服务器和地图，继续展示最终基础五项、战斗面板、预测的 0 转基数及相对基础基数变化量，以及 10 转 99 级预测；失败消息标题为“洗宠结束”，结果仅标记为未达到预期目标，不展示最终属性或预测。每次任务的起止时间保留毫秒，用于避免 PushPlus 将连续任务误判为重复内容。

运行时预测数据已从网页中独立提取，只读取 `ref/pet_bases.json`。宠物技能名称读取自 ClientCoreDLL 中提取的 `ref/skills.xml`。战斗面板统一使用 V15 公式计算，不依赖网页或实测面板库。网页数据更新后，可运行 `python ref/extract_prediction_data.py` 重新生成预测资源文件。

## 目录结构

- 根目录：程序入口 `app.py`、界面及业务模块、依赖清单和使用说明；可执行文件 `mole-respec.exe` 与运行数据库 `pet_results.db` 也位于此处。
- `ref/`：协议参考代码、名称映射、预测和技能数据、界面图片、测试脚本及数据提取脚本。此目录包含程序运行所需资源，不能整体删除。
- `.github/`：Windows 可执行文件构建和 GHCR 镜像发布配置。

在根目录执行 `python -m unittest discover -s ref -p 'test_*.py'` 可运行测试。

## 运行

需要 Python 3.10+。界面使用 PyQt6，协议参考文件依赖 `uncompyle6`。

```powershell
python -m pip install -r requirements.txt
python app.py
```

输入米米号、明文密码和服务器后连接。密码只保存在当前进程内，不会写入文件。

## 飞牛 NAS / Docker 部署

容器使用 Linux Python 直接运行源码，通过 **Xvfb → x11vnc → noVNC** 将原有 PyQt6 界面提供给浏览器，不需要在飞牛上安装桌面或运行 Windows exe。默认虚拟屏幕为 1440×900，包含中文字体；容器内以 UID/GID `1000:1000` 运行，无需特权模式或宿主机显示设备。

### 使用 GitHub 构建并发布镜像

将项目及 `.github/workflows/publish-docker.yml` 推送到 GitHub 后，每次推送都会自动构建并发布镜像。工作流使用 GitHub 自动提供的 `GITHUB_TOKEN` 登录 GHCR，无需额外配置 Token，将该次推送的源码构建为 `linux/amd64` 镜像（适用于 Intel/AMD 飞牛设备），发布到：

```text
ghcr.io/spincat12138/mole-respec:latest
```

所有分支的推送都会更新同一个 `latest` 标签。也可以在仓库 **Actions → Publish Docker image → Run workflow** 手动重新构建。

首次发布后，在 GitHub 的 **Packages → mole-respec → Package settings → Change visibility** 中将包设为 **Public**，飞牛即可免登录拉取。私有镜像需要先使用具有 `read:packages` 权限的个人访问令牌登录 GHCR。如果使用 Fork，请同时修改发布工作流和 `compose.yaml` 中的镜像所属账号。

### 飞牛首次启动

1. 确认上述镜像已发布，将 `compose.yaml` 放入飞牛的一个目录；使用已发布镜像时不需要上传 Python 源码或 Dockerfile。
2. 在 `compose.yaml` 同目录新建 `vnc_password.txt`，填写你自己的 **8 位 ASCII 可见字符密码，不含空格**。不要使用游戏密码。允许文件末尾有换行，包括 Windows 的 CRLF；密码文件已从 Git 和镜像构建上下文中排除。经典 VNC 只支持前 8 字节，因此启动器会明确拒绝过长、过短或非 ASCII 密码。
3. 如需修改端口或分辨率，将 `.env.example` 复制为 `.env` 后编辑；默认浏览器端口为 `6080`。不要把密码写入 `.env`。
4. 在飞牛 Docker 的 Compose 项目中使用该目录与 `compose.yaml`，拉取镜像后启动。也可以通过 SSH 在该目录执行：

```sh
docker compose pull
docker compose up -d
docker compose ps
docker compose logs -f --tail=100
```

首次拉取需要飞牛能访问 `ghcr.io`。Compose 项目必须能读取同目录的 `vnc_password.txt`；如果通过飞牛界面导入，请一并准备密码文件。

浏览器或飞牛 App 直接打开 `http://飞牛IP:6080/` 即可，入口地址无需添加路径或参数。首页会自动跳转到 noVNC，启用自动连接和画面缩放；输入上述 VNC 密码即可操作程序。游戏账号仍在原界面输入。关闭浏览器不会停止洗宠，重新打开可以回到同一会话；多个浏览器连接共享同一个桌面和游戏登录。

默认只发布 `6080`，VNC 的 `5900` 和 X11 不向宿主机开放。此 HTTP 地址用于可信局域网；需要外网访问时，通过 HTTPS 反向代理（启用 WebSocket）或 VPN 访问，不要直接将 `6080` 映射到公网。VNC 密码用于访问控制，不提供传输加密。

### 从源码本地构建

开发调试或不使用 GHCR 时，保留完整仓库和密码文件，显式加载构建覆盖配置：

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

`compose.build.yaml` 将镜像改为本地的 `mole-respec:local` 并启用 `build: .`；后续管理这个本地构建实例时也需带上相同的两个 `-f` 参数。首次构建需要访问 Docker Hub、Debian 软件源和 PyPI。

### 持久化与迁移

Compose 默认创建命名数据卷 `mole-respec-data`（实际名称带 Compose 项目前缀），统一挂载到 `/data`：

| 容器路径 | 内容 |
| --- | --- |
| `/data/pet_results.db` | 洗宠和开蛋记录；原 `/app/pet_results.db` 指向此文件 |
| `/data/config/MoleRespec/MoleRespec.conf` | Qt 设置，包括 PushPlus Token |

普通重启、重新构建、容器重建以及 `docker compose down` 都保留数据。**不要执行 `docker compose down -v`，它会删除数据卷。** Windows 中已有的 PushPlus 设置不会自动复制到 Linux，首次使用请在“杂项”页重新保存 Token。

迁移原来的 `pet_results.db` 时，先结束洗宠并关闭 Windows 程序，将数据库放入项目目录。容器至少创建一次后，在该目录执行以下命令；如果容器已经有记录，先用后面的备份命令保存旧数据，复制操作会替换该数据库：

```sh
docker compose stop
docker compose cp ./pet_results.db mole-respec:/data/pet_results.db
docker compose run --rm --no-deps --user 0 --entrypoint chown mole-respec 1000:1000 /data/pet_results.db
docker compose up -d
```

一致性备份应先结束任务、停止容器，再复制整个 `/data`，完成后重新启动：

```sh
docker compose stop
docker compose cp mole-respec:/data ./mole-respec-backup
docker compose up -d
```

如果希望直接在飞牛文件管理器中访问数据，可将 Compose 中的 `mole-respec-data:/data` 改为 `./data:/data`，并确保该目录及已有文件允许 UID/GID `1000:1000` 读写。更换挂载不会自动迁移旧数据，需先备份再复制到新目录。

### 维护与排查

- 更新：推送代码后，在 GitHub Actions 确认自动构建并发布成功，再在飞牛执行 `docker compose pull` 和 `docker compose up -d`，数据卷继续保留。
- 修改 VNC 密码：更新 `vnc_password.txt` 后执行 `docker compose restart`。
- 调整分辨率：修改 `.env` 的 `SCREEN_WIDTH` / `SCREEN_HEIGHT` 后执行 `docker compose up -d`；范围分别为 `1080–4096`、`824–4096`。浏览器采用缩放方式，不会自动改变虚拟屏幕尺寸。
- 在界面关闭主窗口会使容器退出，Compose 随后按 `unless-stopped` 策略重新启动；需要停止服务时执行 `docker compose stop`。
- 启动失败先看 `docker compose logs --tail=100`。启动器等待 Xvfb、VNC 和 noVNC 就绪后再启动应用；任一进程退出都会结束容器并输出原因，避免只剩空白网页。健康检查同时检测 X11、VNC 与网页。
- `Permission denied`：检查密码文件对容器用户可读，挂载的数据目录可写。默认命名卷已设置目录权限。
- 镜像拉取失败：检查镜像是否已发布、包可见性或 GHCR 登录凭据，以及飞牛到 `ghcr.io` 的网络。本地构建下载失败则检查 Docker Hub、Debian 与 PyPI 的访问。

相关说明：[GitHub 发布 Docker 镜像](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)、[GHCR 权限与登录](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)、[Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)、[noVNC 浏览器连接参数](https://github.com/novnc/noVNC/blob/master/docs/EMBEDDING.md)、[x11vnc 密码文件与监听配置](https://github.com/LibVNC/x11vnc/blob/master/doc/OPTIONS.md)。

## 目标判断

- 只有 0 转宠物可以洗点；转生次数不为 0 时，“开始洗点”不可用，后端也会拒绝请求。
- 绿色成长、红色成长：成长不低于期望值。
- 大丸子：成长加五项；体力、力量、敏捷按不低于判断，耐力、智力按不高于判断。
- 紫色五项、红色五项：不包含成长目标。
- 战斗面板方式：生命、攻击、速度按不低于判断，防御、魔力按不高于判断。
- 期望值填 `0` 表示忽略该项。
- 三组候选均可通过勾选启用，至少需选择一组；每组内的非零条件需同时满足，任一启用的候选组满足时即停止洗点。
- “一键填充”会按当前宠物等级与转生次数生成三组目标。默认加点策略会比较宠物的力量和智力：力量较高时全加力量，否则全加智力；选择“无加点”时不额外加点。三组目标分别填入体力+力量、力量+敏捷、体力+敏捷（或对应的战斗面板），成长均使用默认满成长，其余项置 `0`。

每次任务都有最多洗点次数，道具不足或点击停止时会安全结束循环。
每次洗点成功并读取到最新宠物数据后，程序会同步写入应用目录下的 `pet_results.db`，记录账号、服务器、道具类型和本次洗点结果。
