# Rust brain build handoff

The source architecture is unchanged. `cargo test --manifest-path
infra/brain/Cargo.toml` currently fails before compilation because Cargo cannot
write the host cache under `/Users/jaw/.cargo/registry`, and a clean temporary
`CARGO_HOME` has no cached `axum` index/package.

Run in an environment with network access and a writable Cargo cache:

```sh
export CARGO_HOME=/var/cache/omega-cargo
mkdir -p "$CARGO_HOME"
cargo fetch --locked --manifest-path infra/brain/Cargo.toml
cargo test --locked --manifest-path infra/brain/Cargo.toml
```

The exact dependency set is pinned by `infra/brain/Cargo.lock`; the first
resolver failure is `axum` (with the transitive `async-trait` package also
unavailable locally). No Python rewrite or dependency-version change is
authorized by this handoff.
