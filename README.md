# StreamRecorder 录播字幕搜索

> **使用许可：公开源码，仅供个人非商业使用。未经作者事先书面同意，不得商用、再次发布或分享作者原创代码及其修改版；获准分享时须注明原作者和项目来源。详见 [LICENSE.md](LICENSE.md)。这不是 OSI 定义的开源项目；随附第三方组件各自遵守原许可。

在自己的电脑上同步当前 StreamRecorder 账号可访问的录播列表，给录播生成中文字幕，并按关键词搜索字幕、定位到视频时间戳。项目由 Tampermonkey 用户脚本、本地 Python 服务和网页界面组成；不包含任何账号、录播、字幕或模型数据。

## 工作流程

1. 在 StreamRecorder 登录自己的账号，油猴脚本从该账号的录播页面读取录播列表，并同步到本地服务。
2. 在本地网页选择单个录播生成字幕，或勾选多个录播批量生成。
3. 服务通过当前账号有权使用的下载地址，将完整录播暂存到项目的 `temp/`，用 FFmpeg 提取音频，再用语音识别模型生成带时间戳的字幕，保存到 `subtitles.db`。
4. 正常处理结束后程序会尝试删除临时录播；异常退出或文件占用时可能留下文件，需要手动检查 `temp/`。
5. 在网页中搜索关键词，打开结果并跳转到对应的录播时间点，也可以导出 SRT 字幕。

## 环境要求

- Windows；压缩包随附 `aria2-1.37.0-win-64bit-build1/aria2c.exe`，保持该目录原样即可。若不使用随附版本，也可自行安装 aria2 并加入 PATH。
- Python 3.10 或更新版本。语音模型依赖较大，首次安装和首次下载模型需要一定时间与磁盘空间。
- 已安装 FFmpeg，且在命令提示符运行 `ffmpeg -version` 能成功。
- Tampermonkey 浏览器扩展，以及能访问自己账号录播的 StreamRecorder 登录状态。

在项目目录中安装 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

如果某个模型在当前设备或 Python 环境中无法安装或加载，可先使用页面中的 Paraformer 模型。模型文件在首次使用时下载到本机 `models/`；项目仓库不提供模型权重。

## 启动和同步

1. 在项目目录运行 `python server.py`，保持命令窗口打开。
2. 浏览器访问 `http://127.0.0.1:8000/`，确认本地页面可打开。
3. 在 Tampermonkey 中新建脚本，粘贴 `userscript.js` 的内容并保存。
4. 登录 `https://streamrecorder.io/userrecordings`，在录播页面点击脚本添加的按钮，同步当前账号已有的录播。脚本同步后会打开本地管理页面。
5. 在本地页面生成字幕、批量生成、搜索和定位。关闭命令窗口会停止本地服务。

下载录播需要在项目根目录的 `cookie.txt` 中粘贴当前账号浏览器请求里的**完整 Cookie**（一行，不要只复制 `document.cookie`；应包含 `Authorization` 等登录字段），保存后重启 `python server.py`。如果目录里没有 `cookie.txt`，先新建此文件。首次同步录播列表可以成功，但 Cookie 不完整时生成字幕仍可能报 `Authorization failed`。`cookie.txt` 仅供本机使用，绝对不要上传到 GitHub、发给他人或贴到问题反馈中。`config.json` 也可能保存登录凭据，同样不可共享。Cookie 可能约 30 天后失效；遇到认证失败时应从浏览器重新获取并替换，实际有效期以网站登录状态为准。

## 本地生成的数据

| 路径 | 内容 |
| --- | --- |
| `config.json`、`cookie.txt` | 登录凭据，属于敏感信息 |
| `subtitles.db` | 录播列表、字幕和搜索数据 |
| `models/` | 下载的语音模型缓存 |
| `temp/` | 识别过程中的临时完整录播 |

这些文件由使用者自己在本机生成，不应上传到公开仓库。`.gitignore` 用来防止误提交；手动通过 GitHub 网页上传时仍需自己检查选择的文件。

## 注意

- 只处理自己的账号按网站规则有权访问的录播，不要用工具绕过账号等级、下载配额或其他访问限制。录播内容及生成字幕的权利归属需由使用者自行确认。
- 本工具依赖 StreamRecorder 当前的网页结构和接口；网站更新可能使同步或下载失效。
- 识别结果和时间戳可能有误，尤其是噪声、多人同时说话或专有名词。
- 处理长录播会先占用与视频大小相近的临时磁盘空间。异常中断后请检查 `temp/`，确认不再有任务运行后再清理残留文件。
- 本地服务监听 `127.0.0.1:8000`。请勿把服务直接暴露到公网或共享网络。

## 第三方组件

- `aria2c.exe`：随目录提供；上游源码和发行页见 [aria2 1.37.0](https://github.com/aria2/aria2/releases/tag/release-1.37.0)，许可文件见随附目录中的 `COPYING`、`LICENSE.OpenSSL`，对应源码与构建依赖材料见 `third_party_sources/aria2-1.37.0/`。
- `web/hls.min.js`：hls.js，用于浏览器播放 HLS 录播；其许可见 `web/LICENSE.hls.js`。
- FFmpeg 和语音模型由使用者自行安装或下载，分别遵守其各自许可。FFmpeg 不在本仓库中，下载与许可信息见 [FFmpeg 官网](https://ffmpeg.org/legal.html)。
- 各组件的来源与许可汇总见 [第三方组件说明](THIRD_PARTY_NOTICES.md)。
