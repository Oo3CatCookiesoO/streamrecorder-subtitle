# 第三方组件与许可说明

本项目的作者代码与下列第三方组件分别适用各自的许可。项目随附 aria2；不附带 FFmpeg 可执行文件或语音模型权重。

| 组件 | 本仓库中的位置或使用方式 | 上游及许可 |
| --- | --- | --- |
| aria2 1.37.0 Windows 程序 | `aria2-1.37.0-win-64bit-build1/aria2c.exe` | [aria2 1.37.0 源码与发行页](https://github.com/aria2/aria2/releases/tag/release-1.37.0)，GPL-2.0；随附 `COPYING`、`LICENSE.OpenSSL` 等文件，对应源码包及构建依赖源码见 `third_party_sources/aria2-1.37.0/` |
| hls.js 1.5.13 | `web/hls.min.js` | [hls.js 1.5.13](https://github.com/video-dev/hls.js/releases/tag/v1.5.13)，Apache-2.0；随附 `web/LICENSE.hls.js` 与 `web/APACHE-2.0.txt` |
| FFmpeg | 用户自行安装，程序通过命令行调用 | [FFmpeg 下载页](https://ffmpeg.org/download.html)、[许可说明](https://ffmpeg.org/legal.html)；具体许可取决于所安装版本的编译选项 |
| FunASR 与语音模型 | 用户安装 Python 依赖，首次使用时下载模型 | [FunASR](https://github.com/modelscope/FunASR) 源码与模型权重分别适用各自许可；请查看所下载模型的说明 |
| Qwen3-ASR | 用户安装 Python 依赖，首次使用时下载模型 | [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) 及其模型卡、许可说明 |

分发压缩包时，请保留随附的 aria2、hls.js 版权声明和许可文件，以及 `third_party_sources/aria2-1.37.0/` 中的源码材料。若另行分发 FFmpeg 二进制，须遵守该构建适用的许可。
