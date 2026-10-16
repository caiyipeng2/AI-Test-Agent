#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)
using System;
using System.Globalization;
using System.Reflection;
using System.Threading;
using AiTestAgent.Bridge;
using Hlwd.Framework;
using Hlwd.PB;
using UnityEngine;

namespace AiTestAgent.IdleOutpost
{
    // This adapter only reads initialized controllers and existing raw archive entries.
    public sealed class IdleOutpostReadOnlyProvider : IGameTestObservationProvider
    {
        private static readonly PropertyInfo UpgradeTarget = typeof(UIUpgradeWindow).GetProperty(
            "m_Upgradeable", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
        private readonly GameTestLoadedAssemblyIdentity identity;
        private readonly string buildId;
        private readonly string resourceVersion;
        private readonly int terrainId;
        private readonly int deviceId;
        private readonly int mainThreadId;

        public IdleOutpostReadOnlyProvider(GameTestLoadedAssemblyIdentity identity, string buildId,
            string resourceVersion, int terrainId, int deviceId)
        {
            this.identity = identity;
            this.buildId = buildId;
            this.resourceVersion = resourceVersion;
            this.terrainId = terrainId;
            this.deviceId = deviceId;
            mainThreadId = Thread.CurrentThread.ManagedThreadId;
        }

        public string ProviderId { get { return "IdleOutpostReadOnlyProvider/v1"; } }

        public GameTestObservation ReadObservation()
        {
            if (Thread.CurrentThread.ManagedThreadId != mainThreadId)
                throw new InvalidOperationException("Provider reads require the installing Unity main thread");

            var result = new GameTestObservation();
            if (!Application.isEditor && (Application.platform != RuntimePlatform.Android || !Debug.isDebugBuild))
                return result;
            var request = GameEntity.WebRequest;
            string server = request != null && Enum.IsDefined(typeof(ServerType), request.ServerType)
                ? request.ServerType.ToString() : "";
            if (identity != null) result.source = identity.ReadSource(buildId, resourceVersion, server);

            var user = GameEntity.DataCtrl?.UserData;
            if (user == null) return result;
            var terrainController = GameEntity.Terrain;
            int walletTerrainId = terrainId > 0 ? terrainId : (terrainController?.CurrentTerrainId ?? 0);
            TerrainDiffData terrain = null;
            if (terrainController != null && terrainController.IsLoaded && walletTerrainId > 0 &&
                terrainController.CurrentTerrainId == walletTerrainId && user.TerrainData != null)
                user.TerrainData.TerrainDataDic.TryGetValue(walletTerrainId, out terrain);

            int coin = (int)PropsIdEnum.Coin;
            if (GamePropCfgHelper.Contains(coin))
            {
                GuardDouble balance = null;
                bool hasBalance = GamePropCfgHelper.IsCommonInAllTerrain(coin)
                    ? user.PlayerData != null && user.PlayerData.GameBackPackInfoList.TryGetValue(coin, out balance)
                    : terrain != null && terrain.GameBackPackInfoList.TryGetValue(coin, out balance);
                string text;
                if (hasBalance && !ReferenceEquals(balance, null) && TryFormatAmount(balance.Value, out text))
                    result.wallet = new WalletSnapshot { available = true, currency_id = "Coin", balance = text };
            }

            if (terrainId <= 0 || deviceId <= 0 || terrain == null || GameEntity.Device == null) return result;
            Device device;
            DeviceInfo saved;
            if (!GameEntity.Device.TryGetDeviceById(deviceId, out device) || device == null || device.Id != deviceId ||
                !terrain.DeviceInfos.TryGetValue(deviceId, out saved) || saved == null ||
                device.Lvl < 0 || device.Lvl != saved.Lv || device.MaxLvl <= 0 || device.Lvl > device.MaxLvl)
                return result;
            string cost;
            if (!TryFormatAmount(device.Cost, out cost)) return result;

            bool windowReadSucceeded;
            bool matchesWindow = ReadMatchingWindow(device, out windowReadSucceeded);
            // Schema 2 exposes a target only with its verified, currently open upgrade window.
            if (!windowReadSucceeded || !matchesWindow) return result;

            result.target = new ObservationTargetSnapshot {
                available = true,
                target_id = "terrain:" + terrainId.ToString(CultureInfo.InvariantCulture) +
                    "/device:" + deviceId.ToString(CultureInfo.InvariantCulture),
                window_id = "UIUpgradeWindow",
                level = device.Lvl,
                cost = cost,
                // This only confirms window/target agreement, not every gameplay click prerequisite.
                ready = device.Lvl < device.MaxLvl
            };
            // The game has no authoritative transaction IDs for this adapter's observation window.
            // The default ledger therefore stays unavailable and incomplete.
            return result;
        }

        private static bool ReadMatchingWindow(Device device, out bool succeeded)
        {
            succeeded = true;
            if (GameEntity.UICtrl == null) return false;
            var windows = GameEntity.UICtrl.GetUIWindowsByGroup(UIGroupEnum.PopUp);
            if (windows == null) return false;
            for (var node = windows.First; node != null; node = node.Next)
            {
                var window = node.Value as UIUpgradeWindow;
                if (window == null || !window.IsOpening || window.gameObject == null ||
                    !window.gameObject.activeInHierarchy) continue;
                if (UpgradeTarget == null) { succeeded = false; return false; }
                try
                {
                    if (ReferenceEquals(UpgradeTarget.GetValue(window, null), device)) return true;
                }
                catch (Exception) { succeeded = false; return false; }
            }
            return false;
        }

        private static bool TryFormatAmount(double value, out string text)
        {
            text = "";
            if (double.IsNaN(value) || double.IsInfinity(value) || value < 0) return false;
            // R preserves the source double's round trip; expanding its exponent adds no precision.
            string roundTrip = value.ToString("R", CultureInfo.InvariantCulture);
            int exponentAt = roundTrip.IndexOfAny(new[] { 'E', 'e' });
            if (exponentAt < 0) { text = value == 0 ? "0" : roundTrip; return true; }
            int exponent = int.Parse(roundTrip.Substring(exponentAt + 1), CultureInfo.InvariantCulture);
            string mantissa = roundTrip.Substring(0, exponentAt);
            int decimalAt = mantissa.IndexOf('.');
            int point = (decimalAt < 0 ? mantissa.Length : decimalAt) + exponent;
            string digits = mantissa.Replace(".", "");
            if (point <= 0) text = "0." + new string('0', -point) + digits;
            else if (point >= digits.Length) text = digits + new string('0', point - digits.Length);
            else text = digits.Insert(point, ".");
            return true;
        }
    }
}
#endif
