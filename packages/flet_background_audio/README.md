# Melody background-audio bridge

This package is owned by the Melody repository and ships with the application.
Its Python controls and Dart implementation form one versioned protocol. It is
not maintained as an independently compatible public Flet extension.

The currently paired bridge version is 0.2.0 and the Flet family is pinned to
0.86.5. Change both Python/Dart bridge versions together when the protocol changes;
update Melody's dependency and validate the supported platform matrix in the
same review. Local path resolution is intentional, not a second source of the
package. Dart publication is disabled.

The bridge translates system media controls and publishes native session state.
It does not own Melody's queue, download policy, or Python playback intent. See
[the release and compatibility policy](../../docs/releases-and-dependencies.md)
and [architecture](../../docs/architecture.md) for lifecycle guarantees and gaps.
