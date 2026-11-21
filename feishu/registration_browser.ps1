# Inspect the committed Chrome/Edge document, not editable address-bar text.
# Request and reply are local JSON on stdin/stdout. Never print the launcher URL.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$element = [Windows.Automation.AutomationElement]
$scope = [Windows.Automation.TreeScope]
$control = [Windows.Automation.ControlType]
$walker = [Windows.Automation.TreeWalker]::ControlViewWalker

function Find-By($root, $property, $value) {
    $condition = [Windows.Automation.PropertyCondition]::new($property, $value)
    $root.FindFirst($scope::Descendants, $condition)
}

function Get-Page($window) {
    $doc = Find-By $window $element::ControlTypeProperty $control::Document
    if (!$doc -or $doc.Current.IsOffscreen) { return }
    $value = $doc.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value
    $uri = $null
    if (![uri]::TryCreate($value, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne 'https' -or $uri.Host -ne 'open.feishu.cn' -or !$uri.IsDefaultPort) { return }
    $path = $uri.AbsolutePath.TrimEnd('/')
    if ($request.launcher_code -and ($path -cne '/page/launcher' -or
        [System.Web.HttpUtility]::ParseQueryString($uri.Query)['user_code'] -cne $request.launcher_code)) { return }
    if ($path -cnotin @('/app','/page/launcher') -and $path -cnotmatch '^/app/cli_[A-Za-z0-9_]+/baseinfo$') { return }
    $org = ''
    if ($path -ceq '/app') {
        # In the app list the current organization follows its avatar, outside
        # the table of apps. Account choices, app names and owner names do not count.
        $root = Find-By $doc $element::AutomationIdProperty 'root'
        if (!$root) { return }
        $children = $root.FindAll($scope::Children, [Windows.Automation.Condition]::TrueCondition)
        $labels = @(for ($i = 1; $i -lt $children.Count; $i++) {
            $prev = $children[$i-1].Current
            $item = $children[$i].Current
            if ($prev.ControlType -eq $control::Image -and $prev.ClassName -match '(?:^| )ud__avatar__image(?: |$)' -and
                $item.ControlType -eq $control::Text -and !$item.IsOffscreen) { $item.Name }
        })
        if ($labels.Count -eq 1) { $org = $labels[0] }
    } elseif ($path -cmatch '^/app/cli_[A-Za-z0-9_]+/baseinfo$') {
        $textCondition = [Windows.Automation.PropertyCondition]::new($element::ControlTypeProperty, $control::Text)
        foreach ($label in $doc.FindAll($scope::Descendants, $textCondition)) {
            if (!$label.Current.IsOffscreen -and $label.Current.Name -match '^[^@\uFF20]+[@\uFF20](.+)$' -and
                $walker.GetParent($label).Current.ClassName -match '(?:^| )side-display__info-desc__subtitle(?: |$)') {
                $org = ($label.Current.Name -split '[@\uFF20]', 2)[1].Trim()
                break
            }
        }
    }
    [pscustomobject]@{window=[long]$window.Current.NativeWindowHandle; path=$path; organization=$org}
}

$browserIds = @(Get-Process -Name chrome,msedge -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$windows = $element::RootElement.FindAll($scope::Children, [Windows.Automation.Condition]::TrueCondition)
$found = @(foreach ($window in $windows) {
    if ($window.Current.ProcessId -notin $browserIds -or
        ($request.window -and $window.Current.NativeWindowHandle -ne $request.window)) { continue }
    try { $page = Get-Page $window } catch [Windows.Automation.ElementNotAvailableException] { continue }
    if (!$page) { continue }
    if ($request.show_picker -and $request.window -and $page.path -ceq '/app') {
        $doc = Find-By $window $element::ControlTypeProperty $control::Document
        $closed = Find-By $doc $element::ClassNameProperty '_pp-trigger-container _pp-control-close'
        if ($closed) { $closed.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke() }
        $itemCondition = [Windows.Automation.PropertyCondition]::new($element::ClassNameProperty, '_pp-panel-item')
        foreach ($item in $doc.FindAll($scope::Descendants, $itemCondition)) {
            $text = Find-By $item $element::ControlTypeProperty $control::Text
            if ($text -and $text.Current.Name -in $request.switch_labels -and !$item.Current.IsOffscreen) {
                $invoke = $null
                if ($item.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$invoke)) {
                    $invoke.Invoke()
                }
                break
            }
        }
    }
    if ($request.url) {
        $dest = [uri]$request.url
        if (!$request.window -or $dest.Scheme -ne 'https' -or $dest.Host -ne 'open.feishu.cn' -or
            !$dest.IsDefaultPort -or $dest.UserInfo -or $page.path -cne $request.from_path -or
            ($request.organization -and $page.organization -cne $request.organization)) { continue }
        $launcher = $dest.AbsolutePath -ceq '/page/launcher' -and $page.path -ceq '/app' -and $request.organization
        $detail = $dest.AbsolutePath -cmatch '^/app/cli_[A-Za-z0-9_]+/baseinfo$' -and
            $page.path -cin @('/page/launcher', '/app')
        if (!$launcher -and !$detail) { continue }
        $bar = Find-By $window $element::ClassNameProperty 'OmniboxViewViews'
        if (!$bar -or $bar.Current.ControlType -ne $control::Edit) { continue }
        Add-Type -Namespace Link16 -Name BrowserKeys -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool PostMessage(System.IntPtr hwnd, uint msg, System.IntPtr key, System.IntPtr flags);
'@
        $bar.SetFocus()
        if (!$bar.Current.HasKeyboardFocus) { continue }
        $bar.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue($dest.AbsoluteUri)
        if (!$bar.Current.HasKeyboardFocus) { continue }
        $handle = [IntPtr]$page.window
        if (![Link16.BrowserKeys]::PostMessage($handle, 0x100, [IntPtr]13, [IntPtr]0x001c0001) -or
            ![Link16.BrowserKeys]::PostMessage($handle, 0x101, [IntPtr]13, [IntPtr](-1071906815))) { continue }
    }
    $page
})
ConvertTo-Json -InputObject $found -Compress
