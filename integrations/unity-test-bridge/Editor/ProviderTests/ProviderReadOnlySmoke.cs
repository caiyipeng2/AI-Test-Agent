#if AI_TEST_PROVIDER_STUBS && (UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE))
using System;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Threading;
using AiTestAgent.Bridge;
using Hlwd.PB;

public static class ProviderReadOnlySmoke
{
    private const string Adapter = "AiTestAgent.IdleOutpost.IdleOutpostReadOnlyProvider";
    private const string Bootstrap = "AiTestAgent.IdleOutpost.IdleOutpostBridgeBootstrap";
    private static int passed;

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length != 0)
            {
                Run(args[0], () => BootstrapResourceBoundary(args[0]));
                Console.WriteLine("PROVIDER_RESOURCE_BOUNDARY_PASS " + args[0]);
                return 0;
            }
            Run("missing-data-does-not-insert", MissingDataDoesNotInsert);
            Run("wallet-overloaded-null-equality", WalletOverloadedNullEquality);
            Run("explicit-target-and-window", ExplicitTargetAndWindow);
            Run("upgrade-window-group-isolation", UpgradeWindowGroupIsolation);
            Run("mismatches-unavailable", MismatchesUnavailable);
            Run("wallet-scope-and-missing-keys", WalletScopeAndMissingKeys);
            Run("amounts-are-plain-roundtrip-double", PlainAmounts);
            Run("main-thread-only", MainThreadOnly);
            Run("unsupported-runtime", UnsupportedRuntime);
            Run("bootstrap-mvid-and-server-gates", BootstrapGates);
            Console.WriteLine("PROVIDER_READONLY_SMOKE_PASS cases=" + passed);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }

    private static void Run(string name, Action test) { Reset(); test(); passed++; Console.WriteLine("PASS " + name); }
    private static void Reset()
    {
        GameEntity.DataCtrl = null; GameEntity.Terrain = null; GameEntity.Device = null;
        GameEntity.UICtrl = null; GameEntity.WebRequest = null;
        GamePropCfgHelper.Loaded = false; GamePropCfgHelper.Common = false;
        UnityEngine.Application.isEditor = true;
        UnityEngine.Application.platform = UnityEngine.RuntimePlatform.WindowsEditor;
        UnityEngine.Debug.isDebugBuild = true;
    }
    private static IGameTestObservationProvider Provider(int terrain = 1, int device = 11)
    {
        var type = typeof(ProviderReadOnlySmoke).Assembly.GetType(Adapter);
        Require(type != null, "Real readonly provider is missing");
        return (IGameTestObservationProvider)Activator.CreateInstance(type, new object[] { null, "synthetic-only-build", "12", terrain, device });
    }
    private static UserUpdateData Fixture()
    {
        var user = new UserUpdateData { TerrainData = new TerrainData(), PlayerData = new PlayerData() };
        GameEntity.DataCtrl = new DataCtrl { UserData = user };
        GameEntity.Terrain = new TerrainCtrl { CurrentTerrainId = 1, IsLoaded = true };
        GameEntity.Device = new DeviceCtrl();
        GameEntity.Device.Devices.Add(new Device { Id = 11, Lvl = 2, MaxLvl = 20, Cost = 12.5 });
        GameEntity.UICtrl = new Hlwd.Framework.UICtrl();
        GamePropCfgHelper.Loaded = true;
        return user;
    }
    private static TerrainDiffData Terrain(UserUpdateData user)
    {
        var terrain = new TerrainDiffData(); user.TerrainData.TerrainDataDic.Add(1, terrain);
        terrain.DeviceInfos.Add(11, new DeviceInfo { Lv = 2 });
        return terrain;
    }
    private static void MissingDataDoesNotInsert()
    {
        var provider = Provider();
        var empty = provider.ReadObservation();
        Require(!empty.target.available && !empty.wallet.available && !empty.source.available, "Unknown sections were promoted");
        var user = Fixture();
        provider.ReadObservation(); provider.ReadObservation();
        Require(user.TerrainData.TerrainDataDic.Count == 0 && user.PlayerData.GameBackPackInfoList.Count == 0, "Missing terrain/wallet was inserted");
        var terrain = new TerrainDiffData(); user.TerrainData.TerrainDataDic.Add(1, terrain);
        var missing = provider.ReadObservation();
        Require(!missing.target.available && !missing.wallet.available && terrain.DeviceInfos.Count == 0 && terrain.GameBackPackInfoList.Count == 0, "Missing device/wallet was inserted");
        Require(!missing.ledger.available && !missing.ledger.complete && missing.ledger.events.Length == 0, "Ledger completeness was invented");
    }
    private static void ExplicitTargetAndWindow()
    {
        var user = Fixture(); var terrain = Terrain(user);
        terrain.GameBackPackInfoList.Add((int)PropsIdEnum.Coin, new GuardDouble(50));
        var provider = Provider();
        Require(provider.ProviderId == "IdleOutpostReadOnlyProvider/v1", "Provider identity mismatch");
        var closed = provider.ReadObservation();
        RequireUnavailableTarget(closed.target, "Absent upgrade window");
        Require(closed.wallet.available && closed.wallet.balance == "50", "Missing window hid the independent wallet");
        var window = new UIUpgradeWindow(GameEntity.Device.Devices[0]) { IsOpening = true };
        GameEntity.UICtrl.Windows.AddLast(window);
        var open = provider.ReadObservation();
        Require(open.target.available && open.target.ready && open.target.window_id == "UIUpgradeWindow" && open.target.level == 2 && open.target.cost == "12.5", "Matching open window is unavailable");
        window.gameObject.activeInHierarchy = false;
        RequireUnavailableTarget(provider.ReadObservation().target, "Inactive upgrade window");
        window.gameObject.activeInHierarchy = true; window.IsOpening = false;
        RequireUnavailableTarget(provider.ReadObservation().target, "Closed upgrade window");
        window.IsOpening = true; GameEntity.Device.Devices[0].Lvl = 20; terrain.DeviceInfos[11].Lv = 20;
        var maximum = provider.ReadObservation();
        Require(maximum.target.available && !maximum.target.ready, "Maximum-level target was marked ready");
        var unconfigured = Provider(0, 0).ReadObservation();
        Require(!unconfigured.target.available && unconfigured.wallet.available, "Unconfigured target was guessed or current wallet was lost");
        Require(user.TerrainData.TerrainDataDic.Count == 1 && terrain.DeviceInfos.Count == 1 && terrain.GameBackPackInfoList.Count == 1, "Read changed existing dictionaries");
    }
    private static void WalletOverloadedNullEquality()
    {
        var user = Fixture(); var terrain = Terrain(user); var provider = Provider();
        terrain.GameBackPackInfoList.Add(1, new GuardDouble(50));
        GuardDouble.EqualityCalls = 0;
        var observation = provider.ReadObservation();
        Require(observation.wallet.available && observation.wallet.balance == "50", "Present wallet was not read");
        Require(GuardDouble.EqualityCalls == 0, "Wallet null check invoked value equality overload");
        terrain.GameBackPackInfoList[1] = null;
        Require(!provider.ReadObservation().wallet.available && GuardDouble.EqualityCalls == 0, "Null wallet invoked overloaded equality");
    }

    private static void UpgradeWindowGroupIsolation()
    {
        var user = Fixture(); var terrain = Terrain(user); var provider = Provider();
        var device = GameEntity.Device.Devices[0];
        var window = new UIUpgradeWindow(device) { IsOpening = true };
        Require(!ReferenceEquals(GameEntity.UICtrl.GetUIWindowsByGroup(Hlwd.Framework.UIGroupEnum.Main),
            GameEntity.UICtrl.GetUIWindowsByGroup(Hlwd.Framework.UIGroupEnum.PopUp)), "Synthetic UI groups alias the same list");
        GameEntity.UICtrl.PopUpWindows.AddLast(window);
        RequireUnavailableTarget(provider.ReadObservation().target, "Matching device window in the wrong UI group");
        GameEntity.UICtrl.PopUpWindows.Clear();
        GameEntity.UICtrl.Windows.AddLast(window);
        Require(provider.ReadObservation().target.available, "Configured Main-group window was not found");
        window.IsOpening = false;
        RequireUnavailableTarget(provider.ReadObservation().target, "Closed Main-group window");
        window.IsOpening = true; window.gameObject.activeInHierarchy = false;
        RequireUnavailableTarget(provider.ReadObservation().target, "Inactive Main-group window");
        window.gameObject.activeInHierarchy = true;
        GameEntity.UICtrl.Windows.Clear();
        GameEntity.UICtrl.Windows.AddLast(new UIUpgradeWindow(new Device { Id = device.Id, Lvl = device.Lvl }) { IsOpening = true });
        RequireUnavailableTarget(provider.ReadObservation().target, "Main-group window for a different device object");
        GameEntity.UICtrl.Windows.Clear(); GameEntity.UICtrl.Windows.AddLast(window);
        terrain.DeviceInfos[device.Id].Lv++;
        RequireUnavailableTarget(provider.ReadObservation().target, "Runtime and saved levels differ in the correct UI group");
    }
    private static void MismatchesUnavailable()
    {
        var user = Fixture(); var terrain = Terrain(user); var provider = Provider();
        var matchingWindow = new UIUpgradeWindow(GameEntity.Device.Devices[0]) { IsOpening = true };
        GameEntity.UICtrl.Windows.AddLast(matchingWindow);
        Require(provider.ReadObservation().target.available, "Mismatch fixture lacks a valid initial target");
        terrain.DeviceInfos[11].Lv = 3;
        Require(!provider.ReadObservation().target.available, "Runtime/persisted level mismatch accepted");
        terrain.DeviceInfos[11].Lv = 2; GameEntity.Terrain.CurrentTerrainId = 2;
        Require(!provider.ReadObservation().target.available, "Wrong terrain accepted");
        GameEntity.Terrain.CurrentTerrainId = 1; GameEntity.Terrain.IsLoaded = false;
        Require(!provider.ReadObservation().target.available, "Unloaded terrain accepted");
        GameEntity.Terrain.IsLoaded = true;
        GameEntity.UICtrl.Windows.Clear();
        GameEntity.UICtrl.Windows.AddLast(new UIUpgradeWindow(new Device { Id = 11, Lvl = 2 }) { IsOpening = true });
        RequireUnavailableTarget(provider.ReadObservation().target, "Upgrade window with a different device reference");
        GameEntity.UICtrl.Windows.Clear();
        GameEntity.UICtrl.Windows.AddLast(matchingWindow);
        GameEntity.Device.Devices[0].Lvl = -1; terrain.DeviceInfos[11].Lv = -1;
        Require(!provider.ReadObservation().target.available, "Negative level accepted");
        GameEntity.Device.Devices[0].Lvl = 2; terrain.DeviceInfos[11].Lv = 2;
        GameEntity.Device.Devices[0].Cost = double.NaN;
        Require(!provider.ReadObservation().target.available, "Non-finite target cost accepted");
    }
    private static void WalletScopeAndMissingKeys()
    {
        var user = Fixture(); var terrain = Terrain(user); var provider = Provider();
        user.PlayerData.GameBackPackInfoList.Add(1, new GuardDouble(90));
        Require(!provider.ReadObservation().wallet.available, "Terrain wallet fell back to global");
        terrain.GameBackPackInfoList.Add(1, new GuardDouble(30));
        Require(provider.ReadObservation().wallet.balance == "30", "Terrain wallet scope ignored");
        GamePropCfgHelper.Common = true;
        Require(provider.ReadObservation().wallet.balance == "90", "Common wallet scope ignored");
        user.PlayerData.GameBackPackInfoList.Clear();
        Require(!provider.ReadObservation().wallet.available && user.PlayerData.GameBackPackInfoList.Count == 0, "Missing common wallet was filled");
        GamePropCfgHelper.Loaded = false;
        Require(!provider.ReadObservation().wallet.available, "Unloaded config read allowed");
    }
    private static void PlainAmounts()
    {
        var user = Fixture(); var terrain = Terrain(user); var provider = Provider();
        GameEntity.UICtrl.Windows.AddLast(new UIUpgradeWindow(GameEntity.Device.Devices[0]) { IsOpening = true });
        var amount = new GuardDouble(0); terrain.GameBackPackInfoList.Add(1, amount);
        var previous = CultureInfo.CurrentCulture;
        try
        {
            CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("fr-FR");
            foreach (double value in new[] { 0d, 0.1d, 1e-20d, 1e20d, double.Epsilon, double.MaxValue })
            {
                amount.Value = value; GameEntity.Device.Devices[0].Cost = value;
                var observation = provider.ReadObservation();
                Require(observation.wallet.available && observation.target.available, "Finite amount unavailable");
                foreach (string text in new[] { observation.wallet.balance, observation.target.cost })
                    Require(!text.Contains("E") && !text.Contains("e") && !text.Contains(",") && double.Parse(text, CultureInfo.InvariantCulture) == value, "Amount lost round-trip precision or used exponent/locale");
            }
            foreach (double invalid in new[] { -1d, double.NaN, double.PositiveInfinity, double.NegativeInfinity })
            {
                amount.Value = invalid; GameEntity.Device.Devices[0].Cost = invalid;
                var observation = provider.ReadObservation();
                Require(!observation.wallet.available && !observation.target.available, "Invalid amount accepted");
            }
        }
        finally { CultureInfo.CurrentCulture = previous; }
    }
    private static void MainThreadOnly()
    {
        var provider = Provider(); Exception failure = null;
        var worker = new Thread(() => { try { provider.ReadObservation(); } catch (Exception error) { failure = error; } });
        worker.Start(); worker.Join();
        Require(failure is InvalidOperationException, "Background read was allowed");
    }
    private static void BootstrapGates()
    {
        var type = typeof(ProviderReadOnlySmoke).Assembly.GetType(Bootstrap);
        Require(type != null, "Bootstrap missing");
        byte[] bytes = System.IO.File.ReadAllBytes(type.Assembly.Location);
        ExpectInvocationFailure(() => type.GetMethod("CaptureLoadedAssembly").Invoke(null, new object[] { bytes, Guid.NewGuid().ToString("D") }), "MVID mismatch accepted");
        int before = UnityEngine.GameObject.Created;
        type.GetMethod("Begin").Invoke(null, null);
        Require(UnityEngine.GameObject.Created == before && Hlwd.Aot.ResVersionHelper.Calls == 0, "Missing cached server started bootstrap");
        GameEntity.WebRequest = new Hlwd.Game.WebRequestCtrl { ServerType = ServerType.ProduceServer };
        type.GetMethod("Begin").Invoke(null, null);
        Require(UnityEngine.GameObject.Created == before && Hlwd.Aot.ResVersionHelper.Calls == 0, "Production server started bootstrap");
        GameEntity.WebRequest.ServerType = ServerType.TestServer;
        UnityEngine.Application.platform = UnityEngine.RuntimePlatform.Android;
        UnityEngine.Application.isEditor = false; UnityEngine.Debug.isDebugBuild = false;
        type.GetMethod("Begin").Invoke(null, null);
        Require(UnityEngine.GameObject.Created == before && Hlwd.Aot.ResVersionHelper.Calls == 0, "Non-debug player started bootstrap");
        UnityEngine.Debug.isDebugBuild = true;
        UnityEngine.Application.isEditor = true;
        var capture = type.GetMethod("CaptureLoadedBytes", new[] { typeof(byte[]) });
        Require(capture != null, "Single-argument loaded bytes capture entry is missing");
        capture.Invoke(null, new object[] { bytes });
        type.GetMethod("CaptureLoadedAssembly").Invoke(null, new object[] { bytes, type.Assembly.ManifestModule.ModuleVersionId.ToString("D") });
        AssertEvidence(UnityEngine.Debug.Logs.Last(), "editor_probe", "runtime_api");
        GameEntity.WebRequest.ServerType = ServerType.TestServer;
        type.GetMethod("Begin").Invoke(null, null);
        type.GetMethod("Begin").Invoke(null, null);
        Require(Hlwd.Aot.ResVersionHelper.Calls == 1, "Resource version was not loaded once");
        var publisher = UnityEngine.GameObject.Components.OfType<GameTestObservationPublisher>().Single();
        string line = publisher.PublishOnce();
        Require(line.Contains("synthetic-only-build") && line.Contains("sha256:") && line.Contains("TestServer"), "Captured identity or actual resource/server missing");
        var json = System.Text.Json.JsonDocument.Parse(line.Substring(GameTestObservationPublisher.Marker.Length)).RootElement;
        Require(json.GetProperty("source").GetProperty("resource_version").GetString() == "12", "Resource version did not use actual maximum");
        Require(!json.GetProperty("target").GetProperty("available").GetBoolean(), "Bootstrap guessed a device target");
        var bootstrap = UnityEngine.GameObject.Components.Single(value => value.GetType() == type);
        var update = type.GetMethod("Update", BindingFlags.Instance | BindingFlags.NonPublic);
        int logged = UnityEngine.Debug.Logs.Count;
        UnityEngine.Time.frameCount = 29; update.Invoke(bootstrap, null);
        Require(UnityEngine.Debug.Logs.Count == logged, "Sample interval ignored");
        UnityEngine.Time.frameCount = 30; update.Invoke(bootstrap, null); update.Invoke(bootstrap, null);
        Require(UnityEngine.Debug.Logs.Count == logged + 2, "Same-frame identity/observation pair was absent or duplicated");
        Require(UnityEngine.Debug.Logs[logged].StartsWith("AI_TEST_LOADED_DLL_JSON:") &&
            UnityEngine.Debug.Logs[logged + 1].StartsWith(GameTestObservationPublisher.Marker),
            "Automatic sample must emit its captured identity immediately before observation");
        string sampledIdentity = UnityEngine.Debug.Logs[logged];
        AssertEvidence(sampledIdentity, "editor_probe", "runtime_api");
        UnityEngine.Time.frameCount = 60; update.Invoke(bootstrap, null);
        Require(UnityEngine.Debug.Logs.Count == logged + 4 && UnityEngine.Debug.Logs[logged + 2] == sampledIdentity &&
            UnityEngine.Debug.Logs[logged + 3].StartsWith(GameTestObservationPublisher.Marker),
            "Later sample must retain the loaded identity and emit a new adjacent pair");
        var secondSample = System.Text.Json.JsonDocument.Parse(UnityEngine.Debug.Logs[logged + 3].Substring(GameTestObservationPublisher.Marker.Length)).RootElement;
        Require(secondSample.GetProperty("seq").GetInt64() == 3, "Repeated identity reset observation sequence");
        UnityEngine.Application.isEditor = false; UnityEngine.Application.platform = UnityEngine.RuntimePlatform.WindowsPlayer;
        UnityEngine.Time.frameCount = 90; update.Invoke(bootstrap, null);
        Require(UnityEngine.Debug.Logs.Count == logged + 4, "Unsupported platform continued sampling");
        UnityEngine.Application.isEditor = true;
        GameEntity.WebRequest.ServerType = ServerType.ProduceServer;
        UnityEngine.Time.frameCount = 120; update.Invoke(bootstrap, null);
        Require(UnityEngine.Debug.Logs.Count == logged + 4, "Sampling continued after server changed");
    }
    private static void UnsupportedRuntime()
    {
        var user = Fixture(); var terrain = Terrain(user);
        terrain.GameBackPackInfoList.Add(1, new GuardDouble(50));
        var provider = Provider();
        GameEntity.WebRequest = new Hlwd.Game.WebRequestCtrl { ServerType = ServerType.TestServer };
        var type = typeof(ProviderReadOnlySmoke).Assembly.GetType(Bootstrap);
        byte[] bytes = System.IO.File.ReadAllBytes(type.Assembly.Location);
        int hosts = UnityEngine.GameObject.Created, logs = UnityEngine.Debug.Logs.Count;
        foreach (var platform in new[] { UnityEngine.RuntimePlatform.WindowsPlayer, UnityEngine.RuntimePlatform.IPhonePlayer, UnityEngine.RuntimePlatform.LinuxPlayer })
        {
            UnityEngine.Application.isEditor = false; UnityEngine.Application.platform = platform;
            var observation = provider.ReadObservation();
            Require(!observation.source.available && !observation.target.available && !observation.wallet.available, "Unsupported runtime produced observations: " + platform);
            ExpectInvocationFailure(() => type.GetMethod("CaptureLoadedAssembly").Invoke(null,
                new object[] { bytes, type.Assembly.ManifestModule.ModuleVersionId.ToString("D") }), "Unsupported runtime captured Android identity: " + platform);
            var capture = type.GetMethod("CaptureLoadedBytes", new[] { typeof(byte[]) });
            Require(capture != null, "Single-argument loaded bytes capture entry is missing");
            ExpectInvocationFailure(() => capture.Invoke(null, new object[] { bytes }), "Unsupported runtime captured loaded PE identity: " + platform);
            type.GetMethod("Begin").Invoke(null, null);
            Require(UnityEngine.GameObject.Created == hosts && Hlwd.Aot.ResVersionHelper.Calls == 0 && UnityEngine.Debug.Logs.Count == logs,
                "Unsupported runtime started or emitted identity: " + platform);
        }
    }
    private static void BootstrapResourceBoundary(string mode)
    {
        if (mode == "explicit-target-bootstrap") { BootstrapExplicitTarget(); return; }
        if (mode == "android-pe-bootstrap") { BootstrapAndroidPeIdentity(); return; }
        var type = typeof(ProviderReadOnlySmoke).Assembly.GetType(Bootstrap);
        byte[] bytes = System.IO.File.ReadAllBytes(type.Assembly.Location);
        type.GetMethod("CaptureLoadedAssembly").Invoke(null, new object[] { bytes, type.Assembly.ManifestModule.ModuleVersionId.ToString("D") });
        GameEntity.WebRequest = new Hlwd.Game.WebRequestCtrl { ServerType = ServerType.TestServer };
        int before = UnityEngine.GameObject.Created;
        var completion = new System.Threading.Tasks.TaskCompletionSource<(int, int)>();
        string expectedVersion = null;
        if (mode == "local-resource")
        {
            BM.AssetComponentConfig.AssetLoadMode = BM.AssetLoadMode.Local;
            Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromResult((1200, 7));
            expectedVersion = "7";
        }
        else if (mode == "local-unknown-resource")
        {
            BM.AssetComponentConfig.AssetLoadMode = BM.AssetLoadMode.Local;
            Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromResult((1200, 0));
            expectedVersion = "";
        }
        else if (mode == "build-resource")
        {
            BM.AssetComponentConfig.AssetLoadMode = BM.AssetLoadMode.Build;
            Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromResult((1200, 7));
            expectedVersion = "1200";
        }
        else if (mode == "develop-unknown-resource")
        {
            BM.AssetComponentConfig.AssetLoadMode = BM.AssetLoadMode.Develop;
            Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromResult((1200, 7));
            expectedVersion = "";
        }
        else if (mode == "unknown-resource") Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromResult((0, -1));
        else if (mode == "failed-resource") Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromException<(int, int)>(new InvalidOperationException("Synthetic read failure"));
        else if (mode == "deferred-server-change") Hlwd.Aot.ResVersionHelper.Result = completion.Task;
        else if (mode == "host-destruction-retry") Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromResult((12, 7));
        else throw new ArgumentException("Unknown test boundary");
        type.GetMethod("Begin").Invoke(null, null);
        if (expectedVersion != null)
        {
            var publisher = UnityEngine.GameObject.Components.OfType<GameTestObservationPublisher>().Single();
            string line = publisher.PublishOnce();
            var source = System.Text.Json.JsonDocument.Parse(line.Substring(GameTestObservationPublisher.Marker.Length)).RootElement.GetProperty("source");
            Require(source.GetProperty("resource_version").GetString() == expectedVersion &&
                source.GetProperty("available").GetBoolean() == (expectedVersion != ""),
                "Effective resource version ignored asset load mode: " + mode);
        }
        if (mode == "deferred-server-change")
        {
            Require(UnityEngine.GameObject.Created == before, "Host created before resource read completed");
            GameEntity.WebRequest.ServerType = ServerType.ProduceServer;
            completion.SetResult((12, 7));
            Require(UnityEngine.GameObject.Created == before, "Server change during await was ignored");
        }
        else if (mode == "failed-resource")
            Require(UnityEngine.GameObject.Created == before && UnityEngine.Debug.Logs.Any(line => line.StartsWith("AI_TEST_BRIDGE_UNAVAILABLE:")), "Resource read failure escaped or initialized host");
        else if (mode == "unknown-resource")
        {
            var publisher = UnityEngine.GameObject.Components.OfType<GameTestObservationPublisher>().Single();
            string line = publisher.PublishOnce();
            var source = System.Text.Json.JsonDocument.Parse(line.Substring(GameTestObservationPublisher.Marker.Length)).RootElement.GetProperty("source");
            Require(!source.GetProperty("available").GetBoolean() && source.GetProperty("resource_version").GetString() == "", "Unknown resource version was promoted");
        }
        Require(Hlwd.Aot.ResVersionHelper.Calls == 1, "Resource read count mismatch");
        if (mode == "failed-resource" || mode == "deferred-server-change")
        {
            GameEntity.WebRequest.ServerType = ServerType.TestServer;
            Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromResult((12, 7));
            type.GetMethod("Begin").Invoke(null, null);
            type.GetMethod("Begin").Invoke(null, null);
            Require(Hlwd.Aot.ResVersionHelper.Calls == 2 && UnityEngine.GameObject.Components.OfType<GameTestObservationPublisher>().Count() == 1,
                "Failed initialization could not retry exactly once: " + mode);
        }
        if (mode == "host-destruction-retry")
        {
            type.GetMethod("Begin").Invoke(null, null);
            Require(Hlwd.Aot.ResVersionHelper.Calls == 1, "Successful instance initialized twice");
            var original = UnityEngine.GameObject.Components.OfType<UnityEngine.MonoBehaviour>().Single(value => value.GetType() == type);
            UnityEngine.Object.Destroy(original.gameObject);
            type.GetMethod("Begin").Invoke(null, null);
            type.GetMethod("Begin").Invoke(null, null);
            Require(Hlwd.Aot.ResVersionHelper.Calls == 2 && UnityEngine.GameObject.Components.OfType<GameTestObservationPublisher>().Count() == 1,
                "Destroyed host could not be replaced exactly once");
            type.GetMethod("OnDestroy", BindingFlags.Instance | BindingFlags.NonPublic)?.Invoke(original, null);
            type.GetMethod("Begin").Invoke(null, null);
            Require(Hlwd.Aot.ResVersionHelper.Calls == 2, "Stale host released the replacement's ownership");
            var replacement = UnityEngine.GameObject.Components.Single(value => value.GetType() == type);
            int logs = UnityEngine.Debug.Logs.Count;
            UnityEngine.Time.frameCount = 180;
            type.GetMethod("Update", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(replacement, null);
            Require(UnityEngine.Debug.Logs.Count == logs + 2 &&
                UnityEngine.Debug.Logs[logs].StartsWith("AI_TEST_LOADED_DLL_JSON:") &&
                UnityEngine.Debug.Logs[logs + 1].StartsWith(GameTestObservationPublisher.Marker),
                "Replacement host did not preserve paired readonly sampling");
        }
    }
    private static void BootstrapAndroidPeIdentity()
    {
        var user = Fixture(); var terrain = Terrain(user);
        terrain.GameBackPackInfoList.Add(1, new GuardDouble(50));
        GameEntity.UICtrl.Windows.AddLast(new UIUpgradeWindow(GameEntity.Device.Devices[0]) { IsOpening = true });
        GameEntity.WebRequest = new Hlwd.Game.WebRequestCtrl { ServerType = ServerType.TestServer };
        UnityEngine.Application.isEditor = false;
        UnityEngine.Application.platform = UnityEngine.RuntimePlatform.Android;
        UnityEngine.Debug.isDebugBuild = true;
        var type = typeof(ProviderReadOnlySmoke).Assembly.GetType(Bootstrap);
        byte[] bytes = System.IO.File.ReadAllBytes(type.Assembly.Location);
        try
        {
            type.GetMethod("CaptureLoadedAssembly").Invoke(null, new object[] { bytes, Guid.Empty.ToString("D") });
            throw new InvalidOperationException("Legacy Android entry accepted unsupported runtime module signature");
        }
        catch (TargetInvocationException error)
        {
            Require(error.InnerException is InvalidOperationException,
                "Legacy Android entry must reject before attempting runtime ModuleVersionId validation");
        }
        var capture = type.GetMethod("CaptureLoadedBytes", new[] { typeof(byte[]) });
        Require(capture != null, "Single-argument loaded bytes capture entry is missing");
        UnityEngine.Debug.isDebugBuild = false;
        ExpectInvocationFailure(() => capture.Invoke(null, new object[] { bytes }), "Non-debug Android captured loaded bytes");
        UnityEngine.Debug.isDebugBuild = true;
        capture.Invoke(null, new object[] { bytes });
        string initialIdentity = UnityEngine.Debug.Logs.Last();
        AssertEvidence(initialIdentity, "android_player", "loaded_pe_metadata");
        type.GetMethod("BeginWithTarget").Invoke(null, new object[] { 1, 11 });
        var bootstrap = UnityEngine.GameObject.Components.Single(value => value.GetType() == type);
        var update = type.GetMethod("Update", BindingFlags.Instance | BindingFlags.NonPublic);
        int before = UnityEngine.Debug.Logs.Count;
        foreach (int frame in new[] { 30, 60 })
        {
            UnityEngine.Time.frameCount = frame;
            update.Invoke(bootstrap, null);
            update.Invoke(bootstrap, null);
        }
        Require(UnityEngine.Debug.Logs.Count == before + 4, "Android paired samples were missing or duplicated");
        string session = null;
        for (int index = 0; index < 2; index++)
        {
            string identity = UnityEngine.Debug.Logs[before + index * 2];
            Require(identity == initialIdentity, "Paired Android samples changed the captured PE identity/source");
            AssertEvidence(identity, "android_player", "loaded_pe_metadata");
            string line = UnityEngine.Debug.Logs[before + index * 2 + 1];
            Require(line.StartsWith(GameTestObservationPublisher.Marker), "Android identity was not followed by an observation");
            var state = System.Text.Json.JsonDocument.Parse(line.Substring(GameTestObservationPublisher.Marker.Length)).RootElement;
            Require(state.GetProperty("seq").GetInt64() == index + 1 &&
                state.GetProperty("source").GetProperty("available").GetBoolean() &&
                state.GetProperty("wallet").GetProperty("available").GetBoolean() &&
                state.GetProperty("target").GetProperty("available").GetBoolean(),
                "Android PE identity did not support consecutive readonly source/wallet/target observations");
            string currentSession = state.GetProperty("session_id").GetString();
            Require(session == null || session == currentSession, "Paired identity reset the Android observation session");
            session = currentSession;
        }
        Require(terrain.DeviceInfos[11].Lv == 2 && terrain.GameBackPackInfoList[1].Value == 50,
            "Android identity sampling changed game data");
    }
    private static void AssertEvidence(string line, string runtimeKind, string mvidSource)
    {
        const string marker = "AI_TEST_LOADED_DLL_JSON:";
        Require(line.StartsWith(marker), "Loaded identity marker is absent");
        var evidence = System.Text.Json.JsonDocument.Parse(line.Substring(marker.Length)).RootElement;
        Require(evidence.GetProperty("schema").GetInt32() == 2 &&
            evidence.GetProperty("runtime_kind").GetString() == runtimeKind &&
            evidence.GetProperty("mvid_source").GetString() == mvidSource,
            "Loaded identity schema/runtime/source provenance differs");
    }
    private static void BootstrapExplicitTarget()
    {
        var user = Fixture(); var terrain = Terrain(user);
        terrain.GameBackPackInfoList.Add(1, new GuardDouble(50));
        GameEntity.WebRequest = new Hlwd.Game.WebRequestCtrl { ServerType = ServerType.TestServer };
        var type = typeof(ProviderReadOnlySmoke).Assembly.GetType(Bootstrap);
        var begin = type.GetMethod("BeginWithTarget", new[] { typeof(int), typeof(int) });
        Require(begin != null, "Explicit target bootstrap entry is missing");
        int hosts = UnityEngine.GameObject.Created;
        foreach (var pair in new[] { new[] { 0, 0 }, new[] { 0, 11 }, new[] { 1, 0 }, new[] { -1, 11 }, new[] { 1, -1 } })
            ExpectInvocationFailure(() => begin.Invoke(null, new object[] { pair[0], pair[1] }), "Nonpositive target ID accepted");
        Require(UnityEngine.GameObject.Created == hosts && Hlwd.Aot.ResVersionHelper.Calls == 0,
            "Invalid explicit IDs started a host or read resources");
        byte[] bytes = System.IO.File.ReadAllBytes(type.Assembly.Location);
        type.GetMethod("CaptureLoadedAssembly").Invoke(null, new object[] { bytes, type.Assembly.ManifestModule.ModuleVersionId.ToString("D") });
        begin.Invoke(null, new object[] { 1, 11 });
        begin.Invoke(null, new object[] { 1, 11 });
        type.GetMethod("Begin").Invoke(null, null);
        Require(Hlwd.Aot.ResVersionHelper.Calls == 1 && UnityEngine.GameObject.Components.OfType<GameTestObservationPublisher>().Count() == 1,
            "Explicit/default entry points did not share one active host");
        var publisher = UnityEngine.GameObject.Components.OfType<GameTestObservationPublisher>().Single();
        Func<System.Text.Json.JsonElement> target = () => System.Text.Json.JsonDocument.Parse(
            publisher.PublishOnce().Substring(GameTestObservationPublisher.Marker.Length)).RootElement.GetProperty("target");
        Require(!target().GetProperty("available").GetBoolean(), "Explicit target bypassed missing window verification");
        var wrong = new UIUpgradeWindow(new Device { Id = 11, Lvl = 2, MaxLvl = 20, Cost = 12.5 }) { IsOpening = true };
        GameEntity.UICtrl.Windows.AddLast(wrong);
        Require(!target().GetProperty("available").GetBoolean(), "Different device object with matching ID was accepted");
        GameEntity.UICtrl.Windows.AddLast(new UIUpgradeWindow(GameEntity.Device.Devices[0]) { IsOpening = true });
        var actual = target();
        Require(actual.GetProperty("available").GetBoolean() && actual.GetProperty("target_id").GetString() == "terrain:1/device:11" &&
            actual.GetProperty("window_id").GetString() == "UIUpgradeWindow" && actual.GetProperty("level").GetInt64() == 2,
            "Explicit IDs were not bound to the matching existing device/window");
        GameEntity.Terrain.CurrentTerrainId = 2;
        Require(!target().GetProperty("available").GetBoolean(), "Explicit target crossed the current terrain boundary");
        Require(user.TerrainData.TerrainDataDic.Count == 1 && terrain.DeviceInfos[11].Lv == 2 &&
            terrain.GameBackPackInfoList[1].Value == 50, "Explicit target sampling changed game data");
    }
    private static void ExpectInvocationFailure(Action action, string message)
    {
        try { action(); } catch (TargetInvocationException error) { if (error.InnerException is ArgumentException || error.InnerException is InvalidOperationException) return; throw; }
        throw new InvalidOperationException(message);
    }
    private static void RequireUnavailableTarget(ObservationTargetSnapshot target, string scenario)
    {
        Require(!target.available && target.target_id == "" && target.window_id == "" && target.level == -1 &&
            target.cost == "" && !target.ready, scenario + " must retain schema2 unavailable target markers");
    }
    private static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
}
#endif
