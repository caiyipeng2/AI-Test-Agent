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
        type.GetMethod("CaptureLoadedAssembly").Invoke(null, new object[] { bytes, type.Assembly.ManifestModule.ModuleVersionId.ToString("D") });
        Require(UnityEngine.Debug.Logs.Any(line => line.StartsWith("AI_TEST_LOADED_DLL_JSON:") && line.Contains("android_player")), "Player capture kind missing");
        UnityEngine.Application.isEditor = true;
        type.GetMethod("CaptureLoadedAssembly").Invoke(null, new object[] { bytes, type.Assembly.ManifestModule.ModuleVersionId.ToString("D") });
        Require(UnityEngine.Debug.Logs.Any(line => line.StartsWith("AI_TEST_LOADED_DLL_JSON:") && line.Contains("editor_probe")), "Editor capture kind missing");
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
        Require(UnityEngine.Debug.Logs.Count == logged + 1, "Same-frame sample was duplicated");
        UnityEngine.Application.isEditor = false; UnityEngine.Application.platform = UnityEngine.RuntimePlatform.WindowsPlayer;
        UnityEngine.Time.frameCount = 60; update.Invoke(bootstrap, null);
        Require(UnityEngine.Debug.Logs.Count == logged + 1, "Unsupported platform continued sampling");
        UnityEngine.Application.isEditor = true;
        GameEntity.WebRequest.ServerType = ServerType.ProduceServer;
        UnityEngine.Time.frameCount = 90; update.Invoke(bootstrap, null);
        Require(UnityEngine.Debug.Logs.Count == logged + 1, "Sampling continued after server changed");
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
            type.GetMethod("Begin").Invoke(null, null);
            Require(UnityEngine.GameObject.Created == hosts && Hlwd.Aot.ResVersionHelper.Calls == 0 && UnityEngine.Debug.Logs.Count == logs,
                "Unsupported runtime started or emitted identity: " + platform);
        }
    }
    private static void BootstrapResourceBoundary(string mode)
    {
        var type = typeof(ProviderReadOnlySmoke).Assembly.GetType(Bootstrap);
        byte[] bytes = System.IO.File.ReadAllBytes(type.Assembly.Location);
        type.GetMethod("CaptureLoadedAssembly").Invoke(null, new object[] { bytes, type.Assembly.ManifestModule.ModuleVersionId.ToString("D") });
        GameEntity.WebRequest = new Hlwd.Game.WebRequestCtrl { ServerType = ServerType.TestServer };
        int before = UnityEngine.GameObject.Created;
        var completion = new System.Threading.Tasks.TaskCompletionSource<(int, int)>();
        if (mode == "unknown-resource") Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromResult((0, -1));
        else if (mode == "failed-resource") Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromException<(int, int)>(new InvalidOperationException("Synthetic read failure"));
        else if (mode == "deferred-server-change") Hlwd.Aot.ResVersionHelper.Result = completion.Task;
        else if (mode == "host-destruction-retry") Hlwd.Aot.ResVersionHelper.Result = System.Threading.Tasks.Task.FromResult((12, 7));
        else throw new ArgumentException("Unknown test boundary");
        type.GetMethod("Begin").Invoke(null, null);
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
        }
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
