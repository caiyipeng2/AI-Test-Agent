# Idle_Outpost 勘察与安装证据摘要

日期：2026-10-09，Asia/Shanghai。

本报告区分本轮实测事实、历史参考和未验证项。它不是首次升级或 AI 测试的通过报告。

## 项目勘察

| 项目 | 本轮发现 |
| --- | --- |
| 用户指定源码目录 | `D:\Project\Idle_Outpost` |
| Unity | `2022.3.62f2` |
| 主要技术 | UGUI、HybridCLR、BundleMaster、Protobuf |
| 测试依赖 | Unity Test Framework `1.1.33` |
| 游戏产品名 | Idle Weapon Shop Tycoon |
| Android 包名 | `com.hg.idleweaponshoptycoon.android` |
| 源码分支 | `feature/Medal-Challenge` |
| 源码 HEAD | `6e93c0841ff9e0e4f902c0953c4671f49c109190` |
| 工作区 | 存在大量本地修改和未跟踪文件；落后原游戏远端 1 提交 |
| CodeGraph | 提升权限后 `codegraph sync` 返回 0，同步 4 个文件；结构化查询可用 |

没有对游戏工作区执行清理、切分支、拉取、暂存或提交。CodeGraph 同步属于索引维护，不是游戏实现变更。

可复用的源码接入点：

| 位置（相对游戏仓库） | 用途及局限 |
| --- | --- |
| `Packages/Hlwd-Framework/Runtime/UIFramework/UICtrl.cs:345` | 当前打开窗口、顶层窗口、Canvas，可作为测试桥状态来源 |
| `Assets/Scripts_Hotfix/GameCtrls/NodeCtrl/DeviceCtrl.cs:276` | 设备查询与等级状态，可作为升级的业务断言来源 |
| `Assets/Scripts_Hotfix/GameCtrls/Guide/GuideCtrl.Task.cs:104` | 当前引导任务/阶段，可验证是否被引导阻挡 |
| `Assets/Scripts_Hotfix/GameCtrls/WebRequest/WebRequestCtrl.Request.cs:42` | 使用设备标识登录，清除本地数据不代表新账号 |
| `Assets/Editor/Build/AndroidAutoTestQuickBuild.cs:19` | Android 热更 DLL/资源快捷构建辅助，不是运行时测试桥 |

本轮定向检查未发现可用的 `QA_PING`、`QA_STATE`、TestAgent 或 HTTP/TCP 测试状态桥。源码配置指向 TestServer，且代码、资源热更启用；这不能证明下述 release 包实际使用相同配置。

## 包体选择

包体目录：`D:\飞书存储\AAB_Install\AAB_Install`。目录混有其他产品，依据 manifest 与项目包名匹配选择包体，不能仅按全目录更新时间安装。

本轮匹配项目的最新候选：

| 项目 | 内容 |
| --- | --- |
| AAB | `idle_weaponshop_haiwai_v65_2026_09_23_21_55-release.aab` |
| AAB 修改时间 | 2026-09-23 22:44:37 |
| AAB 大小 | 350,915,612 字节 |
| AAB SHA-256 | `01D72F114D51EF7E9DE1ACECCA89AA1B09C95F99B037AD0438158EF29B8A93EC` |
| APKS | `idle_weaponshop_haiwai_v65_2026_09_23_21_55-release.apks` |
| APKS 修改时间 | 2026-09-23 22:48:17 |
| APKS 大小 | 532,648,408 字节 |
| APKS SHA-256 | `959EA9BFDFE3FA78F6D83A6A83E9C05958C4ADC4B6157C3757C0A63154B92152` |
| Manifest | AAB 与 APKS 中的 base master 均为目标包名，versionCode `65`、versionName `2.1.1` |
| SDK/ABI | minSdk `24`、targetSdk `36`，提供 arm64/v7a 分片 |

同名 AAB/APKS 的元数据一致并不等于已验证所有文件之间的生成关系。未来构建流水线需要记录 AAB→APKS 的生成日志和签名指纹。本轮安装对象是现成 APKS，并未重新打包或签名。

## 安装实测

两台设备当前均处于 ADB `device` 状态：Samsung SM-S9280（S24 Ultra，API 36，arm64）与 moto g 2025（API 35，arm64）。选择已有主测 S24 Ultra 做本轮更新安装，另一台未操作。设备序列号保存在本地，不进入公开报告。

| 检查 | 结果 |
| --- | --- |
| 安装前参考设备 | versionCode `64`，versionName `2.1.0` |
| 安装工具 | 现有 Java、bundletool.jar、ADB，未安装新依赖 |
| 安装方式 | `bundletool install-apks --apks=<v65 APKS> --device-id=<reference serial> --adb=<ADB>` |
| 命令退出码 | `0` |
| 安装后 `dumpsys package` | versionCode `65`，versionName `2.1.1` |
| 安装后更新时间（设备报告） | 2026-10-09 16:30:48 |
| `firstInstallTime` | 仍为 2026-10-08 17:19:59 |
| 已安装分片 | `base.apk`、`split_UnityDataAssetPack.apk`、`split_config.arm64_v8a.apk` |
| Activity 解析 | `com.unity3d.player.UnityPlayerActivity` |
| 数据操作 | 更新安装，没有执行卸载或 `pm clear`；未验证云存档是否发生变化 |

安装状态：**PASS（仅安装层）**。Activity 能解析并不证明实际启动或登录通过。

APKS 分片选择和部署方式参考 [Android 官方 bundletool 文档](https://developer.android.com/tools/bundletool)。

原始证据位于本地 `artifacts/2026-10-09-install/`，包括：

- `install-output.txt`：安装工具输出。
- `install-exit-code.txt`：退出码。
- `installed-package.txt`：安装后的版本与时间。
- `installed-splits.txt`：分片路径。
- `launch-activity.txt`：Activity 解析结果。

上述目录被 Git 忽略；公开仓库只提交本摘要。

## 历史参考与本轮限制

游戏仓库 `Reports/AndroidRealDeviceTestContract.md` 和 `AndroidRealDeviceStartupSmoke.md` 记录 2026-09-01 的旧 v63 路线：安装、启动、登录、主界面、打开升级、首次升级。旧探索曾受协议弹窗及游戏内弹窗阻挡，并发现原生 UI hierarchy 只暴露 Unity Game view。本轮没有复用旧结果作为新包通过证据。

尚未验证：

1. v65 的启动、实际服务器、账号进度、资源版本及热更 DLL 身份。
2. v65 是否包含当前 2026-10-09 源码；两者日期不同，缺少构建来源证明。
3. 当前首次升级的可操作位置、资源门槛和升级前后业务状态。
4. AI 用例生成、Agent 调用、误报分类和评测指标。

进入新手业务操作前，要明确隔离账号、服务器和重置方式。原源码中的测试服配置、包名相同、版本号更新均不足以代替运行时环境验证。
