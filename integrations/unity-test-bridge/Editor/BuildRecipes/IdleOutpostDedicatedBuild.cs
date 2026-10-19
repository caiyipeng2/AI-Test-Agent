#if UNITY_EDITOR && AI_TEST_IDLE_OUTPOST_BUILD
// Project-specific recipe for a disposable source/SDK copy. Never run in the original project.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using BM;
using Hlwd.Aot;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace AiTestAgent.BuildRecipes
{
    public static class IdleOutpostDedicatedBuild
    {
        private static readonly string RunRoot = ReadBuildRoot();
        private static readonly string ProjectRoot = Path.Combine(RunRoot, "GameProject");
        private static readonly string AndroidRoot = Path.Combine(RunRoot, "AndroidBuild");
        private static readonly string ExportRoot = Path.Combine(RunRoot, "UnityExport");
        private const string LaunchScene = "Assets/Scenes/Launch.unity";
        private const int ResourceVersion = 1019;
        private const string BridgeDefine = "AI_TEST_AGENT_BRIDGE";
        private static readonly List<string> Errors = new List<string>();

        [Serializable]
        public sealed class PhaseSummary
        {
            public int schema = 1;
            public string phase;
            public string status = "RUNNING";
            public string started_utc;
            public string finished_utc;
            public string project_root = ProjectRoot;
            public string android_root = AndroidRoot;
            public string export_root = ExportRoot;
            public string unity_version;
            public string version_name;
            public int version_code;
            public int resource_version = ResourceVersion;
            public int prior_resource_version_preference;
            public bool development;
            public string asset_load_mode;
            public string server;
            public string build_result = "NOT_RUN";
            public ulong build_total_bytes;
            public int build_errors;
            public int build_warnings;
            public string dll_path;
            public string dll_sha256;
            public string module_mvid;
            public string runtime_build_id;
            public string exception_type;
            public string exception_message;
            public string[] error_logs;
        }

        public static void Configure() { Run("configure", ConfigureCore); }
        public static void BuildResources() { Run("resources", BuildResourcesCore); }
        public static void ExportPlayer() { Run("export", ExportPlayerCore); }

        private static void Run(string phase, Action<PhaseSummary> body)
        {
            var summary = new PhaseSummary {
                phase = phase,
                started_utc = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture),
                unity_version = Application.unityVersion
            };
            Errors.Clear();
            Application.logMessageReceived += CaptureError;
            try
            {
                AssertProjectBoundary();
                body(summary);
                if (Errors.Count != 0) throw new InvalidOperationException("Unity emitted error or exception logs during this phase");
                FillConfiguration(summary);
                summary.status = "PASS";
            }
            catch (Exception error)
            {
                summary.status = "FAIL";
                summary.exception_type = error.GetType().FullName;
                summary.exception_message = error.Message;
                Debug.LogException(error);
            }
            finally
            {
                Application.logMessageReceived -= CaptureError;
                summary.finished_utc = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture);
                summary.error_logs = Errors.ToArray();
                try { WriteSummary(summary); }
                catch (Exception error)
                {
                    summary.status = "FAIL";
                    Debug.LogError("Dedicated build summary could not be written: " + error.GetType().Name);
                }
            }
            Debug.Log("AI_TEST_DEDICATED_BUILD_PHASE " + phase + " " + summary.status);
            EditorApplication.Exit(summary.status == "PASS" ? 0 : 1);
        }

        private static void ConfigureCore(PhaseSummary summary)
        {
            Require(EditorUserBuildSettings.activeBuildTarget == BuildTarget.Android,
                "Start Unity with -buildTarget Android before invoking Configure");
            string[] defines = PlayerSettings.GetScriptingDefineSymbolsForGroup(BuildTargetGroup.Android).Split(';');
            Require(defines.Contains(BridgeDefine), "AI_TEST_AGENT_BRIDGE must be configured before this Unity process starts");
            Require(defines.Contains("USE_HYBIRDCLR"), "HybridCLR scripting define is missing");
            summary.prior_resource_version_preference = ResVersionHelper.ResVersionInEditor;

            EditorUserBuildSettings.development = true;
            EditorUserBuildSettings.allowDebugging = false;
            EditorUserBuildSettings.connectProfiler = false;
            EditorUserBuildSettings.buildWithDeepProfilingSupport = false;
            EditorUserBuildSettings.buildScriptsOnly = false;
            EditorUserBuildSettings.androidBuildSystem = AndroidBuildSystem.Gradle;
            EditorUserBuildSettings.exportAsGoogleAndroidProject = true;
            EditorUserBuildSettings.buildAppBundle = true;
            PlayerSettings.SetScriptingBackend(BuildTargetGroup.Android, ScriptingImplementation.IL2CPP);
            PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64 | AndroidArchitecture.ARMv7;
            PlayerSettings.Android.useAPKExpansionFiles = true;
            PlayerSettings.SetAdditionalIl2CppArgs("--linker-flags=\"-Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384\"");
            ApplyOptionalVersionOverrides();

            var export = AssetDatabase.LoadAssetAtPath<ExportSettingSo>("Assets/Editor/Build/ExportSetting.asset");
            Require(export != null, "Export setting asset is missing");
            export.dirAndroidStudio = AndroidRoot;
            EditorUtility.SetDirty(export);
            AssetDatabase.SaveAssetIfDirty(export);
            AssertExportBoundary();

            var config = AssetDatabase.LoadAssetAtPath<HwGameConfig>(HwGameConfig.HwGameConfigPath);
            Require(config != null, "Game config asset is missing");
            config.SetServer(ServerType.TestServer);
            config._enableResHotfix = false;
            config.EnableCodeHotfix = true;
            config.IsTestResHotfix = true;
            var serialized = new SerializedObject(config);
            var standalone = serialized.FindProperty("_isStandAloneInAndroid");
            Require(standalone != null && !standalone.boolValue, "This validation requires the existing online Android TestServer mode");
            EditorUtility.SetDirty(config);
            AssetDatabase.SaveAssetIfDirty(config);
            BuildProject.SetAbLoadMode();

            var table = AssetDatabase.LoadAssetAtPath<AssetLoadTable>(BundleMasterWindow.AssetLoadTablePath);
            Require(table != null && !table.EnableRelativePath, "Bundle output must use the copied project's relative directory");
            AssertWithinRoot(table.BuildBundlePath);
            Require(File.Exists(AssertWithinRoot(Path.Combine(ProjectRoot, LaunchScene))), "Launch scene is missing");
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(LaunchScene, true) };
            BuildProject.ResetAllStepData();
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            AssertConfiguration();
        }

        private static void BuildResourcesCore(PhaseSummary summary)
        {
            AssertConfiguration();
            CopyHotfixDll.CompileDllActiveBuildTarget();
            CopyHotfixDll.GenerateAll();
            // GenerateAll recompiles. Copy the final DLL only after its last compile.
            CopyHotfixDll.CopyDllToFolder(AssertWithinRoot(Path.Combine(Application.dataPath, "ResBundles_Dll")));
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            RecordHotfix(summary);
            CopyHotfixDll.CopyAotDllsToResBundle_Dll();
            AssertCopiedAotMetadata();
            BM.BuildAssets.BuildAllBundle();

            int priorVersionPreference = ResVersionHelper.ResVersionInEditor;
            summary.prior_resource_version_preference = priorVersionPreference;
            try
            {
                ResVersionHelper.ResVersionInEditor = ResourceVersion;
                AssertWithinRoot(ResVersionHelper.VersionFileBuildPath);
                ResVersionHelper.CreateVersionFileInEditor();
            }
            finally { ResVersionHelper.ResVersionInEditor = priorVersionPreference; }
            BundleMasterEditor.CopyFileToTargetFolder(AssertWithinRoot(Application.streamingAssetsPath));
            ResVersionHelper.CopyVersionFileToStreamingAssets();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            AssertVersionFiles();
            AssertHotfixMatches(summary);
        }

        private static void ExportPlayerCore(PhaseSummary summary)
        {
            AssertConfiguration();
            AssertVersionFiles();
            var resources = ReadSummary("resources");
            Require(resources.status == "PASS", "A successful resources phase is required");
            AssertHotfixMatches(resources);
            summary.dll_path = resources.dll_path;
            summary.dll_sha256 = resources.dll_sha256;
            summary.module_mvid = resources.module_mvid;
            summary.runtime_build_id = resources.runtime_build_id;
            Directory.CreateDirectory(AssertWithinRoot(ExportRoot));
            EditorUserBuildSettings.exportAsGoogleAndroidProject = true;
            EditorUserBuildSettings.development = true;
            var report = BuildPipeline.BuildPlayer(new[] { LaunchScene }, AssertWithinRoot(ExportRoot),
                BuildTarget.Android, BuildOptions.AcceptExternalModificationsToPlayer | BuildOptions.Development);
            Require(report != null, "Unity returned no BuildReport");
            summary.build_result = report.summary.result.ToString();
            summary.build_total_bytes = report.summary.totalSize;
            summary.build_errors = report.summary.totalErrors;
            summary.build_warnings = report.summary.totalWarnings;
            Require(report.summary.result == BuildResult.Succeeded, "Android Unity export failed: " + report.summary.result);
            AssertHotfixMatches(resources);
            Require(Directory.Exists(AssertWithinRoot(Path.Combine(ExportRoot, "unityLibrary/src/main/Il2CppOutputProject"))),
                "Fresh IL2CPP export is missing");
            Require(File.Exists(AssertWithinRoot(Path.Combine(ExportRoot, "settings.gradle"))), "Unity Gradle export is missing");
        }

        private static void RecordHotfix(PhaseSummary summary)
        {
            string source = HotfixPath();
            string asset = AssertWithinRoot(Path.Combine(Application.dataPath, "ResBundles_Dll/hlwdgame.bytes"));
            Require(File.Exists(source) && File.Exists(asset), "Compiled hotfix DLL or copied asset is missing");
            string hash = Sha256(source);
            Require(hash == Sha256(asset), "Final hotfix DLL and packaged bytes differ");
            string buildId;
            string mvid;
            AppDomain.CurrentDomain.ReflectionOnlyAssemblyResolve += ResolveMetadataDependency;
            try
            {
                var assembly = Assembly.ReflectionOnlyLoad(File.ReadAllBytes(source));
                Require(assembly.GetName().Name == "hlwd.game", "Unexpected hotfix assembly name");
                Type stampType = assembly.GetType("AiTestAgent.Generated.ProviderBuildStamp", true);
                FieldInfo stamp = stampType.GetField("Value", BindingFlags.Public | BindingFlags.Static);
                Require(stamp != null && stamp.IsLiteral, "Generated build stamp constant is missing");
                buildId = stamp.GetRawConstantValue() as string;
                Require(!string.IsNullOrEmpty(buildId) && buildId.Length <= 256, "Generated build stamp is empty or too long");
                Require(assembly.GetType("AiTestAgent.IdleOutpost.IdleOutpostBridgeBootstrap", false) != null,
                    "The development/bridge compilation did not include the bootstrap");
                mvid = assembly.ManifestModule.ModuleVersionId.ToString("D");
            }
            finally { AppDomain.CurrentDomain.ReflectionOnlyAssemblyResolve -= ResolveMetadataDependency; }
            string outputDirectory = AssertWithinRoot(Path.Combine(RunRoot, "Artifacts", "Resources", Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(outputDirectory);
            string frozenDll = Path.Combine(outputDirectory, "hlwd.game.dll");
            File.Copy(source, frozenDll, false);
            Require(hash == Sha256(frozenDll), "Frozen DLL hash differs from compiled DLL");
            summary.dll_path = frozenDll;
            summary.dll_sha256 = hash;
            summary.module_mvid = mvid;
            summary.runtime_build_id = buildId;
        }

        private static Assembly ResolveMetadataDependency(object sender, ResolveEventArgs args)
        {
            var expected = new AssemblyName(args.Name);
            var loaded = AppDomain.CurrentDomain.ReflectionOnlyGetAssemblies()
                .FirstOrDefault(value => value.GetName().FullName == expected.FullName);
            if (loaded != null) return loaded;
            var candidates = new List<string>();
            foreach (Assembly executionAssembly in AppDomain.CurrentDomain.GetAssemblies())
                if (!executionAssembly.IsDynamic && executionAssembly.GetName().FullName == expected.FullName &&
                    !string.IsNullOrEmpty(executionAssembly.Location)) candidates.Add(executionAssembly.Location);
            candidates.Add(Path.Combine(Path.GetDirectoryName(HotfixPath()), expected.Name + ".dll"));
            candidates.Add(Path.Combine(ProjectRoot, "Library/ScriptAssemblies", expected.Name + ".dll"));
            candidates.Add(Path.Combine(EditorApplication.applicationContentsPath, "Managed/UnityEngine", expected.Name + ".dll"));
            candidates.Add(Path.Combine(EditorApplication.applicationContentsPath, "Managed", expected.Name + ".dll"));
            candidates.Add(Path.Combine(EditorApplication.applicationContentsPath, "MonoBleedingEdge/lib/mono/4.5/Facades", expected.Name + ".dll"));
            foreach (string candidate in candidates.Distinct(StringComparer.OrdinalIgnoreCase))
                if (File.Exists(candidate) && AssemblyName.GetAssemblyName(candidate).FullName == expected.FullName)
                    return Assembly.ReflectionOnlyLoadFrom(candidate);
            throw new FileNotFoundException("A matching metadata-only dependency is unavailable: " + expected.Name);
        }

        private static void AssertCopiedAotMetadata()
        {
            foreach (string name in HwGameConfig.Instance.PatchAOTAssemblies)
            {
                string source = AssertWithinRoot(Path.Combine(ProjectRoot, "HybridCLRData/AssembliesPostIl2CppStrip/Android", name + ".dll"));
                string asset = AssertWithinRoot(Path.Combine(Application.dataPath, "ResBundles_Dll/aotDll", name + ".bytes"));
                Require(File.Exists(source) && File.Exists(asset), "AOT metadata is missing: " + name);
                Require(Sha256(source) == Sha256(asset), "Copied AOT metadata differs: " + name);
            }
        }

        private static void AssertConfiguration()
        {
            AssertProjectBoundary();
            AssertExportBoundary();
            Require(EditorUserBuildSettings.activeBuildTarget == BuildTarget.Android && EditorUserBuildSettings.development,
                "Android development configuration was not preserved");
            Require(PlayerSettings.Android.bundleVersionCode == 67 && PlayerSettings.bundleVersion == "2.1.2",
                "Dedicated package version metadata must remain 67 / 2.1.2");
            Require(PlayerSettings.GetScriptingBackend(BuildTargetGroup.Android) == ScriptingImplementation.IL2CPP,
                "The copied project must use IL2CPP");
            Require(PlayerSettings.GetScriptingDefineSymbolsForGroup(BuildTargetGroup.Android).Split(';').Contains(BridgeDefine),
                "The Android bridge define is absent");
            Require(!BuildProject.AutoBuild, "Legacy automatic build continuation must remain disabled");
            var config = AssetDatabase.LoadAssetAtPath<HwGameConfig>(HwGameConfig.HwGameConfigPath);
            var serialized = new SerializedObject(config);
            Require(config != null && serialized.FindProperty("_server").intValue == (int)ServerType.TestServer &&
                !config._enableResHotfix && config.EnableCodeHotfix, "Dedicated test package configuration drifted");
            var bmConfig = AssetDatabase.LoadAssetAtPath<BundleMasterRuntimeConfig>("Assets/Resources/BMConfig.asset");
            Require(bmConfig != null && bmConfig.AssetLoadMode == AssetLoadMode.Local, "Dedicated package must read bundled local resources");
        }

        private static void AssertHotfixMatches(PhaseSummary expected)
        {
            Require(!string.IsNullOrEmpty(expected.dll_path) && File.Exists(AssertWithinRoot(expected.dll_path)), "Frozen final DLL evidence is missing");
            Require(Sha256(expected.dll_path) == expected.dll_sha256 && Sha256(HotfixPath()) == expected.dll_sha256 &&
                Sha256(AssertWithinRoot(Path.Combine(Application.dataPath, "ResBundles_Dll/hlwdgame.bytes"))) == expected.dll_sha256,
                "Hotfix output changed after its final resource-build record");
        }

        private static void AssertVersionFiles()
        {
            foreach (string path in new[] { ResVersionHelper.VersionFileBuildPath,
                Path.Combine(Application.streamingAssetsPath, ResVersionConst.RES_VERSION_FILE) })
                Require(File.ReadAllText(AssertWithinRoot(path)).Trim() == ResourceVersion.ToString(CultureInfo.InvariantCulture),
                    "Resource version file differs from the dedicated rebuild version");
        }

        private static string ReadBuildRoot()
        {
            string root = Environment.GetEnvironmentVariable("AI_TEST_BUILD_ROOT");
            if (string.IsNullOrWhiteSpace(root) || !Path.IsPathRooted(root))
                throw new InvalidOperationException("AI_TEST_BUILD_ROOT must name an absolute dedicated build directory");
            string full = Path.GetFullPath(root);
            string suppliedRoot = Path.GetPathRoot(root).Replace(Path.AltDirectorySeparatorChar, Path.DirectorySeparatorChar);
            if (!string.Equals(suppliedRoot, Path.GetPathRoot(full), StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("AI_TEST_BUILD_ROOT cannot depend on the current drive or drive working directory");
            return full;
        }

        private static void AssertProjectBoundary()
        {
            Require(SamePath(Path.GetDirectoryName(Application.dataPath), ProjectRoot), "This harness may run only in its fixed private copied project");
            AssertWithinRoot(Application.dataPath);
        }

        private static void AssertExportBoundary()
        {
            string target = ExportSettingHelper.GetAndroidStudioPath();
            Require(SamePath(target, AndroidRoot), "Build postprocessing must target only the private Android SDK copy");
            AssertWithinRoot(target);
        }

        private static void FillConfiguration(PhaseSummary summary)
        {
            summary.version_name = PlayerSettings.bundleVersion;
            summary.version_code = PlayerSettings.Android.bundleVersionCode;
            summary.development = EditorUserBuildSettings.development;
            summary.asset_load_mode = "Local";
            summary.server = "TestServer";
        }

        private static void ApplyOptionalVersionOverrides()
        {
            // Explicit dedicated-package metadata, independent of stale source or SDK defaults.
            PlayerSettings.Android.bundleVersionCode = 67;
            PlayerSettings.bundleVersion = "2.1.2";
            string code = Environment.GetEnvironmentVariable("AI_TEST_BUILD_VERSION_CODE");
            if (!string.IsNullOrEmpty(code))
            {
                int parsed;
                Require(int.TryParse(code, NumberStyles.None, CultureInfo.InvariantCulture, out parsed) && parsed == 67,
                    "AI_TEST_BUILD_VERSION_CODE must equal the reviewed dedicated package code 67");
                PlayerSettings.Android.bundleVersionCode = parsed;
            }
            string name = Environment.GetEnvironmentVariable("AI_TEST_BUILD_VERSION_NAME");
            if (!string.IsNullOrEmpty(name))
            {
                Require(name == "2.1.2", "AI_TEST_BUILD_VERSION_NAME must equal the reviewed API-compatible value 2.1.2");
                PlayerSettings.bundleVersion = name;
            }
        }

        private static string HotfixPath()
        {
            return AssertWithinRoot(Path.Combine(ProjectRoot, "HybridCLRData/HotUpdateDlls/Android/hlwd.game.dll"));
        }

        private static PhaseSummary ReadSummary(string phase)
        {
            string path = SummaryPath(phase);
            Require(File.Exists(path), "Required phase summary is missing: " + phase);
            var summary = UnityEngine.JsonUtility.FromJson<PhaseSummary>(File.ReadAllText(path));
            Require(summary != null && summary.schema == 1 && summary.phase == phase, "Invalid phase summary");
            return summary;
        }

        private static void WriteSummary(PhaseSummary summary)
        {
            string path = SummaryPath(summary.phase);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, UnityEngine.JsonUtility.ToJson(summary, true) + "\n");
        }

        private static string SummaryPath(string phase)
        {
            Require(phase == "configure" || phase == "resources" || phase == "export", "Unknown build phase");
            return AssertWithinRoot(Path.Combine(RunRoot, "Harness", "results", phase + ".summary.json"));
        }

        private static string Sha256(string path)
        {
            using (var stream = File.OpenRead(AssertWithinRoot(path)))
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }

        private static string AssertWithinRoot(string path)
        {
            string full = Path.GetFullPath(path);
            string root = Path.GetFullPath(RunRoot).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            Require(full.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase), "Output escapes the fixed private run directory");
            return full;
        }

        private static bool SamePath(string left, string right)
        {
            return !string.IsNullOrEmpty(left) && !string.IsNullOrEmpty(right) &&
                string.Equals(Path.GetFullPath(left).TrimEnd('\\', '/'), Path.GetFullPath(right).TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase);
        }

        private static void CaptureError(string condition, string stackTrace, LogType kind)
        {
            if ((kind == LogType.Error || kind == LogType.Exception || kind == LogType.Assert) && Errors.Count < 20)
                Errors.Add(condition.Length <= 4096 ? condition : condition.Substring(0, 4096));
        }

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }
    }
}
#endif
