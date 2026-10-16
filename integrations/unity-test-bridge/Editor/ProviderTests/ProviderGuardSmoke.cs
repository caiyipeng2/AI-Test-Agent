#if AI_TEST_PROVIDER_STUBS && !(UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE))
using System;

public static class ProviderGuardSmoke
{
    public static int Main()
    {
        var assembly = typeof(ProviderGuardSmoke).Assembly;
        foreach (string type in new[] { "AiTestAgent.IdleOutpost.IdleOutpostReadOnlyProvider",
            "AiTestAgent.IdleOutpost.IdleOutpostBridgeBootstrap", "AiTestAgent.Bridge.GameTestObservationPublisher",
            "AiTestAgent.Bridge.GameTestLoadedAssemblyIdentity" })
            if (assembly.GetType(type) != null) throw new InvalidOperationException("Excluded runtime type leaked: " + type);
        Console.WriteLine("PROVIDER_GUARD_SMOKE_PASS");
        return 0;
    }
}
#endif
