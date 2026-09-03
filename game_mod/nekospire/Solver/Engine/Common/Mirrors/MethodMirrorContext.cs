namespace CombatSolver.Engine.Common.Mirrors;

internal interface IMethodMirrorContext<in TBase>
    where TBase : class
{
    PredictionTrace.TraceScope PushDispatchSource(TBase receiver, MirrorMethodSpec method);

    void RecordMethodNotMirroredRisk();

    void RecordMethodMirrorIncompleteRisk();
}
