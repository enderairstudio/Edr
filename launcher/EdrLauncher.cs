using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class EdrLauncher
{
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
        string appDir = Path.Combine(root, "app");
        string script = Path.Combine(appDir, "command.py");

        if (!File.Exists(script))
        {
            Console.Error.WriteLine("EDR error: missing application files. Reinstall EDR.");
            return 1;
        }

        string pythonExe;
        string pythonArgs;
        if (!TryResolvePython(root, out pythonExe, out pythonArgs))
        {
            Console.Error.WriteLine("EDR needs Python 3.11+ on this PC.");
            Console.Error.WriteLine("Install from https://www.python.org/downloads/ and check \"Add to PATH\".");
            Console.Error.WriteLine("Or run: winget install Python.Python.3.11");
            return 1;
        }

        var cmd = new StringBuilder();
        cmd.Append(pythonArgs);
        cmd.Append('"').Append(script).Append('"');
        foreach (string arg in args)
        {
            cmd.Append(' ');
            cmd.Append(QuoteArgument(arg));
        }

        string workDir = Directory.GetCurrentDirectory();
        if (string.IsNullOrWhiteSpace(workDir) || !Directory.Exists(workDir))
        {
            workDir = appDir;
        }

        var start = new ProcessStartInfo
        {
            FileName = pythonExe,
            Arguments = cmd.ToString(),
            WorkingDirectory = workDir,
            UseShellExecute = false,
        };
        start.Environment["PYTHONIOENCODING"] = "utf-8";
        start.Environment["PYTHONUTF8"] = "1";

        using (Process process = Process.Start(start))
        {
            process.WaitForExit();
            return process.ExitCode;
        }
    }

    static bool TryResolvePython(string root, out string exe, out string prefixArgs)
    {
        prefixArgs = "";

        string bundled = Path.Combine(root, "python", "python.exe");
        if (File.Exists(bundled))
        {
            exe = bundled;
            return true;
        }

        if (CommandWorks("py", "-3 -c \"import sys; raise SystemExit(0)\""))
        {
            exe = "py";
            prefixArgs = "-3 ";
            return true;
        }

        foreach (string name in new[] { "python3", "python" })
        {
            string path = FindOnPath(name);
            if (path != null && CommandWorks(path, "-c \"import sys; raise SystemExit(0)\""))
            {
                exe = path;
                return true;
            }
        }

        exe = null;
        return false;
    }

    static string FindOnPath(string fileName)
    {
        string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (string dir in pathEnv.Split(';'))
        {
            if (string.IsNullOrWhiteSpace(dir))
            {
                continue;
            }
            string candidate = Path.Combine(dir.Trim(), fileName);
            if (File.Exists(candidate))
            {
                return candidate;
            }
            if (!fileName.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
            {
                candidate = candidate + ".exe";
                if (File.Exists(candidate))
                {
                    return candidate;
                }
            }
        }
        return null;
    }

    static bool CommandWorks(string exe, string arguments)
    {
        try
        {
            var start = new ProcessStartInfo
            {
                FileName = exe,
                Arguments = arguments,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using (Process process = Process.Start(start))
            {
                process.WaitForExit(5000);
                return process.ExitCode == 0;
            }
        }
        catch
        {
            return false;
        }
    }

    static string QuoteArgument(string value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return "\"\"";
        }
        if (value.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
        {
            return value;
        }
        return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }
}
