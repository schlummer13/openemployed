# ── Build ──
FROM rust:1-bookworm AS build
WORKDIR /src
# Abhängigkeiten zuerst (Layer-Cache)
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs && echo '' > src/lib.rs \
    && mkdir migrations && cargo build --release --locked 2>/dev/null || true
COPY src ./src
COPY migrations ./migrations
RUN touch src/main.rs src/lib.rs && cargo build --release --locked

# ── Laufzeit: minimal, ohne Shell, als nicht-root ──
FROM gcr.io/distroless/cc-debian12:nonroot
WORKDIR /app
COPY --from=build /src/target/release/ledger /app/ledger
USER nonroot
EXPOSE 8080
ENTRYPOINT ["/app/ledger"]
