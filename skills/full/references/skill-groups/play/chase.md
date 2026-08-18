# Chase procedure

Use the reviewed theater-of-the-mind chase facade in Play. Start with exact
participants, quarry, speeds, source-reviewed contextual adjustments, and
initial separation.

For every turn, query current chase state and supply the declared turn action,
complication handling, prone recovery, and exact quarry-visibility map required
by the facade. The server owns dice, movement, exhaustion, complications,
revision, and random position.

End with an audited outcome and commit resulting scene facts separately. A
chase and Combat may never both be active: call `chase(action="end")`, re-query
and verify that no chase is active, refresh the native tool list/exposure for
Play, and only then call `combat_start` when the fiction starts an encounter.
