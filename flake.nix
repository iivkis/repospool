{
  description = "RepoSpool: a standalone repository snapshot generator";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        repospool = pkgs.rustPlatform.buildRustPackage {
          pname = "repospool";
          version = "1.0.0";
          src = ./.;
          cargoLock.lockFile = ./Cargo.lock;
          nativeBuildInputs = [ pkgs.makeWrapper pkgs.git ];
          postFixup = ''
            wrapProgram "$out/bin/repospool" --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.git ]}
          '';
          meta = with pkgs.lib; {
            description = "Self-contained UTF-8 Git source snapshots for coding agents";
            license = licenses.mit;
            mainProgram = "repospool";
            platforms = platforms.unix;
          };
        };
      in {
        packages = {
          inherit repospool;
          default = repospool;
        };
        apps.default = {
          type = "app";
          program = "${repospool}/bin/repospool";
        };
        apps.repospool = {
          type = "app";
          program = "${repospool}/bin/repospool";
        };
        checks.default = repospool;
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [ cargo rustc rustfmt clippy git go-task ] ++ [ repospool ];
          RUST_BACKTRACE = "1";
        };
      });
}
