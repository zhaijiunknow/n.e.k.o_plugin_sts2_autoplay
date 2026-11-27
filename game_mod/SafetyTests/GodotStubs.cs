// Only logging/path resolution are stubbed. Persistence uses real temp files.
namespace Godot;
internal static class GD { public static void PrintErr(string message) => Console.Error.WriteLine(message); }
internal static class ProjectSettings { public static string GlobalizePath(string path) => throw new NotSupportedException(); }
