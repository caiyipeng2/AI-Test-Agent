#if AI_TEST_PROVIDER_STUBS
// Synthetic API shapes only. Never include this file in the actual game assembly.
using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading.Tasks;

namespace UnityEngine
{
    public class Object
    {
        public static void DontDestroyOnLoad(Object value) { }
        public static void Destroy(Object value)
        {
            var host = value as GameObject;
            if (host == null) return;
            foreach (object component in new List<object>(GameObject.Components))
            {
                var behaviour = component as MonoBehaviour;
                if (behaviour == null || !ReferenceEquals(behaviour.gameObject, host)) continue;
                component.GetType().GetMethod("OnDestroy", BindingFlags.NonPublic | BindingFlags.Instance)?.Invoke(component, null);
                GameObject.Components.Remove(component);
            }
        }
        public static void DestroyImmediate(Object value) { Destroy(value); }
    }
    public class MonoBehaviour : Object { public GameObject gameObject; }
    public sealed class GameObject : Object
    {
        public bool activeInHierarchy = true;
        public static int Created;
        public static readonly List<object> Components = new List<object>();
        public GameObject(string name) { Created++; }
        public T AddComponent<T>() where T : MonoBehaviour, new()
        {
            var component = new T { gameObject = this };
            Components.Add(component);
            typeof(T).GetMethod("OnEnable", BindingFlags.NonPublic | BindingFlags.Instance)?.Invoke(component, null);
            return component;
        }
    }
    public sealed class ExecuteAlways : Attribute { }
    public enum RuntimePlatform { WindowsEditor, WindowsPlayer, IPhonePlayer, Android, LinuxPlayer }
    public static class Application { public static bool isEditor = true; public static RuntimePlatform platform = RuntimePlatform.WindowsEditor; }
    public static class Debug
    {
        public static bool isDebugBuild = true;
        public static readonly List<string> Logs = new List<string>();
        public static void Log(object value) { Logs.Add(value.ToString()); }
        public static void LogWarning(object value) { Logs.Add(value.ToString()); }
    }
    public static class Time { public static int frameCount; }
    public static class JsonUtility
    {
        public static string ToJson(object value) { return System.Text.Json.JsonSerializer.Serialize(value,
            new System.Text.Json.JsonSerializerOptions { IncludeFields = true }); }
    }
}
namespace UnityEngine.Scripting { public sealed class PreserveAttribute : Attribute { } }
namespace AiTestAgent.Generated { public static class ProviderBuildStamp { public const string Value = "synthetic-only-build"; } }
#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)
namespace AiTestAgent.Bridge { public static class UgUiSnapshot { public static WindowSnapshot ReadActive() { return new WindowSnapshot(); } } }
#endif
namespace Hlwd.PB
{
    public sealed class GuardDouble
    {
        public double Value;
        public static int EqualityCalls;
        public GuardDouble(double value) { Value = value; }
        // Match the real API's value-based operators, including their inability to compare null operands.
        private static double ReadOperand(GuardDouble operand)
        {
            if (ReferenceEquals(operand, null)) throw new NullReferenceException("Synthetic GuardDouble null operand");
            return operand.Value;
        }
        public static bool operator ==(GuardDouble left, GuardDouble right) { EqualityCalls++; return ReadOperand(left) == ReadOperand(right); }
        public static bool operator !=(GuardDouble left, GuardDouble right) { EqualityCalls++; return ReadOperand(left) != ReadOperand(right); }
        public static bool operator ==(GuardDouble left, double right) { EqualityCalls++; return ReadOperand(left) == right; }
        public static bool operator !=(GuardDouble left, double right) { EqualityCalls++; return ReadOperand(left) != right; }
        public override bool Equals(object other) { return ReferenceEquals(this, other); }
        public override int GetHashCode() { return base.GetHashCode(); }
    }
    public sealed class DeviceInfo { public int Lv; }
    public sealed class TerrainDiffData
    {
        public readonly Dictionary<int, DeviceInfo> DeviceInfos = new Dictionary<int, DeviceInfo>();
        public readonly Dictionary<int, GuardDouble> GameBackPackInfoList = new Dictionary<int, GuardDouble>();
    }
    public sealed class TerrainData { public readonly Dictionary<int, TerrainDiffData> TerrainDataDic = new Dictionary<int, TerrainDiffData>(); }
    public sealed class PlayerData { public readonly Dictionary<int, GuardDouble> GameBackPackInfoList = new Dictionary<int, GuardDouble>(); }
    public sealed class UserUpdateData { public TerrainData TerrainData; public PlayerData PlayerData; }
}
namespace Hlwd.Framework
{
    public enum UIGroupEnum { Main = 100, PopUp = 200 }
    public class UIWindow { public bool IsOpening; public UnityEngine.GameObject gameObject = new UnityEngine.GameObject("synthetic-window"); }
    public sealed class UICtrl
    {
        // Match the actual window-4 configuration: UIUpgradeWindow belongs to Main (100).
        public readonly LinkedList<UIWindow> Windows = new LinkedList<UIWindow>();
        public readonly LinkedList<UIWindow> PopUpWindows = new LinkedList<UIWindow>();
        public LinkedList<UIWindow> GetUIWindowsByGroup(UIGroupEnum group)
        {
            return group == UIGroupEnum.Main ? Windows : (group == UIGroupEnum.PopUp ? PopUpWindows : null);
        }
    }
}
namespace Hlwd.Game { public sealed class WebRequestCtrl { public ServerType ServerType; } }
namespace Hlwd.Aot
{
    public static class ResVersionHelper
    {
        public static int Calls;
        public static Task<(int, int)> Result = Task.FromResult((12, 7));
        public static Task<(int, int)> LoadLocalResVersion() { Calls++; return Result; }
    }
}
namespace BM
{
    public enum AssetLoadMode { Develop = 0, Local = 1, Build = 2 }
    public static class AssetComponentConfig { public static AssetLoadMode AssetLoadMode = AssetLoadMode.Build; }
}
public enum ServerType { LocalServer = 0, TestServer = 2, ProduceServer = 3 }
public enum PropsIdEnum { Coin = 1 }
public sealed class DataCtrl { public Hlwd.PB.UserUpdateData UserData; }
public sealed class TerrainCtrl { public int CurrentTerrainId; public bool IsLoaded; }
public sealed class Device { public int Id; public int Lvl; public int MaxLvl; public double Cost; }
public sealed class DeviceCtrl
{
    public readonly List<Device> Devices = new List<Device>();
    public bool TryGetDeviceById(int id, out Device device) { device = Devices.Find(value => value.Id == id); return device != null; }
}
public static class GameEntity
{
    public static DataCtrl DataCtrl;
    public static TerrainCtrl Terrain;
    public static DeviceCtrl Device;
    public static Hlwd.Framework.UICtrl UICtrl;
    public static Hlwd.Game.WebRequestCtrl WebRequest;
}
public static class GamePropCfgHelper
{
    public static bool Loaded;
    public static bool Common;
    public static bool Contains(int id) { return Loaded && id == (int)PropsIdEnum.Coin; }
    public static bool IsCommonInAllTerrain(int id) { if (!Loaded) throw new InvalidOperationException("Config not loaded"); return Common; }
}
public sealed class UIUpgradeWindow : Hlwd.Framework.UIWindow
{
    private Device m_Upgradeable { get; set; }
    public UIUpgradeWindow(Device device) { m_Upgradeable = device; }
}
#endif
