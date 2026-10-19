param([string]$OutputDirectory, [string]$DefineConstants = 'UNITY_EDITOR;AI_TEST_PROVIDER_STUBS')
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repo '.local\provider-smoke' }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$repoXml = [System.Security.SecurityElement]::Escape($repo)
$project = @"
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType><TargetFramework>net9.0</TargetFramework>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <DefineConstants>$DefineConstants</DefineConstants>
    <NoWarn>CS0649</NoWarn><NuGetAudit>false</NuGetAudit>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="$repoXml\integrations\unity-test-bridge\Runtime\GameTestStatePublisher.cs" />
    <Compile Include="$repoXml\integrations\unity-test-bridge\Runtime\GameTestObservationPublisher.cs" />
    <Compile Include="$repoXml\integrations\unity-test-bridge\Runtime\GameTestLoadedAssemblyIdentity.cs" Condition="Exists('$repoXml\integrations\unity-test-bridge\Runtime\GameTestLoadedAssemblyIdentity.cs')" />
    <Compile Include="$repoXml\integrations\unity-test-bridge\Runtime\GameTestPeModuleIdentity.cs" />
    <Compile Include="$repoXml\integrations\unity-test-bridge\Adapters\IdleOutpost\*.cs" />
    <Compile Include="$repoXml\integrations\unity-test-bridge\Editor\ProviderTests\*.cs" />
  </ItemGroup>
</Project>
"@
$projectPath = Join-Path $OutputDirectory 'ProviderSmoke.csproj'
[System.IO.File]::WriteAllText($projectPath, $project)
$env:GIT_OPTIONAL_LOCKS = '0'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
& dotnet build $projectPath --configuration Release --nologo --verbosity quiet
if ($LASTEXITCODE -ne 0) { throw "Synthetic provider smoke did not compile (exit $LASTEXITCODE)" }
& dotnet (Join-Path $OutputDirectory 'bin\Release\net9.0\ProviderSmoke.dll')
if ($LASTEXITCODE -ne 0) { throw "Synthetic provider smoke failed (exit $LASTEXITCODE)" }
if ($DefineConstants.Contains('UNITY_EDITOR') -or ($DefineConstants.Contains('DEVELOPMENT_BUILD') -and $DefineConstants.Contains('AI_TEST_AGENT_BRIDGE'))) {
    foreach ($boundary in @('unknown-resource', 'failed-resource', 'deferred-server-change', 'host-destruction-retry', 'local-resource', 'local-unknown-resource', 'build-resource', 'develop-unknown-resource', 'explicit-target-bootstrap', 'android-pe-bootstrap')) {
        & dotnet (Join-Path $OutputDirectory 'bin\Release\net9.0\ProviderSmoke.dll') $boundary
        if ($LASTEXITCODE -ne 0) { throw "Synthetic resource boundary failed: $boundary (exit $LASTEXITCODE)" }
    }
}
