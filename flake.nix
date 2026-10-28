{
  description = "RGP reproducible Rust development environment with Python/uv";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            cargo
            rustc
            rustfmt
            clippy
            rust-analyzer
            pkg-config
            git
            python313
            uv
            just
            go-task
          ];
          RUST_BACKTRACE = "1";
          UV_PYTHON = "${pkgs.python313}/bin/python3";
          shellHook = ''
            echo "RGP dev shell: cargo, rustfmt, clippy, Python, uv, task and just available"
          '';
        };
      });
}
