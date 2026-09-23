{ pkgs }:

pkgs.buildEnv {
  name = "sandkasten-tools";

  paths = with pkgs; [
    vale-ls
    zathura
    meld
    git-lfs

    (aspellWithDicts (d: with d; [
      en
      en-computers
      # en-science # unfree license
      de
    ]))

    gdal
    gettext
    gh

    (python3.withPackages (ps: with ps; [
      matplotlib
      numpy
      ipykernel
      jupyter
    ]))

    markdown-oxide
    marksman
    opencode
    pdftk
    tectonic
    vale
    julia-mono
  ];
}
