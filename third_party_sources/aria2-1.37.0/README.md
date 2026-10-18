# aria2 1.37.0 Windows 构建对应源码

此目录随本项目中的 `aria2-1.37.0-win-64bit-build1/aria2c.exe` 一起分发。该可执行文件与 [aria2 官方 1.37.0 Windows 发行包](https://github.com/aria2/aria2/releases/tag/release-1.37.0) 中的文件 SHA-256 一致。源码版本与官方发布标签相同；六个依赖版本来自该标签内的 `Dockerfile.mingw` 构建说明。

aria2 及其依赖分别遵守各自源码包内的许可。此目录不受本项目作者的个人非商业许可限制。

| 文件 | 官方来源 | SHA-256 |
| --- | --- | --- |
| `aria2-release-1.37.0-source.zip` | https://github.com/aria2/aria2/archive/refs/tags/release-1.37.0.zip | `f0458eed12529086260c40b5054ee5d2fcf762b2b9dbe18487f56eee6153df7d` |
| `c-ares-1.19.1.tar.gz` | https://github.com/c-ares/c-ares/releases/download/cares-1_19_1/c-ares-1.19.1.tar.gz | `321700399b72ed0e037d0074c629e7741f6b2ec2dda92956abe3e9671d3e268e` |
| `expat-2.5.0.tar.bz2` | https://github.com/libexpat/libexpat/releases/download/R_2_5_0/expat-2.5.0.tar.bz2 | `6f0e6e01f7b30025fa05c85fdad1e5d0ec7fd35d9f61b22f34998de11969ff67` |
| `gmp-6.3.0.tar.xz` | https://gmplib.org/download/gmp/gmp-6.3.0.tar.xz | `a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898` |
| `libssh2-1.11.0.tar.bz2` | https://libssh2.org/download/libssh2-1.11.0.tar.bz2 | `e56d81f01f090d3c6eaf5f56c645610fd1a90d92819541d0079ae363d1eed11e` |
| `sqlite-autoconf-3430100.tar.gz` | https://www.sqlite.org/2023/sqlite-autoconf-3430100.tar.gz | `39116c94e76630f22d54cd82c3cea308565f1715f716d1b2527f1c9c969ba4d9` |
| `zlib-1.3.tar.gz` | https://www.zlib.net/fossils/zlib-1.3.tar.gz | `ff0ba4c292013dbc27530b3a81e1f9a813cd39de01ca5e0f8bf355702efa593e` |

随附 aria2 可执行文件 SHA-256：`
be2099c214f63a3cb4954b09a0becd6e2e34660b886d4c898d260febfe9d70c2
`。

构建方式见 `aria2-release-1.37.0-source.zip` 中的 `Dockerfile.mingw`，64 位构建使用 `HOST=x86_64-w64-mingw32`。
