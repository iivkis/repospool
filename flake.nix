{
  description = "RGP: a standalone repository snapshot generator";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        rgp = pkgs.rustPlatform.buildRustPackage {
          pname = "rgp";
          version = "0.1.0";
          src = ./.;
          cargoLock.lockFile = ./Cargo.lock;
          nativeBuildInputs = [ pkgs.makeWrapper pkgs.git ];
          postFixup = ''
            wrapProgram "$out/bin/rgp" --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.git ]}
          '';
          meta = with pkgs.lib; {
            description = "Self-contained UTF-8 Git source snapshots for coding agents";
            license = licenses.mit;
            mainProgram = "rgp";
            platforms = platforms.unix;
          };
        };
      in {
        packages = {
          inherit rgp;
          default = rgp;
        };
        apps.default = {
          type = "app";
          program = "${rgp}/bin/rgp";
        };
        apps.rgp = {
          type = "app";
          program = "${rgp}/bin/rgp";
        };
        checks.default = rgp;
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [ cargo rustc rustfmt clippy git go-task ] ++ [ rgp ];
          RUST_BACKTRACE = "1";
        };
      });
}
