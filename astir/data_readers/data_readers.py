import warnings

warnings.simplefilter(action="ignore", category=FutureWarning)

import yaml
import os
import loompy
import anndata

import numpy as np
import pandas as pd

from sklearn.preprocessing import OneHotEncoder
from astir.astir import Astir
import torch


def from_csv_yaml(
    csv_input: str,
    marker_yaml: str,
    design_csv=None,
    random_seed=1234,
    include_beta=False,
    dtype=torch.float64,
):
    """Create an Astir object from an expression CSV and marker YAML

    :param csv_input: Path to input csv containing expression for cells (rows) by proteins (columns). First column is 
        cell identifier, and additional column names are gene identifiers.
    :param marker_yaml: Path to input YAML file containing marker gene information. Should include cell_type and cell_state      
        entries. See documention.
    :param design_csv: Path to design matrix as a CSV. Rows should be cells, and columns covariates. First column is cell 
        identifier, and additional column names are covariate identifiers.
    """
    df_gex = pd.read_csv(csv_input, index_col=0)

    design = None
    if design_csv is not None:
        design = pd.read_csv(design_csv, index_col=0)
    with open(marker_yaml, "r") as stream:
        marker_dict = yaml.safe_load(stream)
    return Astir(df_gex, marker_dict, design, random_seed, include_beta, dtype=dtype)


def from_csv_dir_yaml(
    input_dir: str,
    marker_yaml: str,
    random_seed=1234,
    include_beta=False,
    dtype=torch.float64,
):
    """Create an Astir object a directory containing multiple csv files

    TODO add text explaining concatenation

    :param input_dir: Path to a directory containing multiple CSV files, each in the format expected by
        `from_csv_yaml`
    :param marker_yaml: Path to input YAML file containing marker gene information. Should include cell_type and cell_state      
        entries. See documention.
    :param design_csv: Path to design matrix as a CSV. Rows should be cells, and columns covariates. First column is cell 
        identifier, and additional column names are covariate identifiers.
    """
    # Parse the input directory
    csv_files = [
        os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.endswith("csv")
    ]

    # Read to gene expression df and parse
    dfs = [pd.read_csv(f, index_col=0) for f in csv_files]
    df_gex = pd.concat(dfs, axis=0)

    # Construct a sample specific design matrix
    design_list = [np.repeat(str(i), dfs[i].shape[0]) for i in range(len(dfs))]
    design = (
        OneHotEncoder()
        .fit_transform(np.concatenate(design_list, axis=0).reshape(-1, 1))
        .todense()
    )
    design = design[:, :-1]  # remove final column
    design = np.concatenate(
        [np.ones((design.shape[0], 1)), design], axis=1
    )  # add in intercept!

    with open(marker_yaml, "r") as stream:
        marker_dict = yaml.safe_load(stream)

    return Astir(df_gex, marker_dict, design, random_seed, include_beta, dtype)


def from_loompy_yaml(
    loom_file: str,
    marker_yaml: str,
    protein_name_attr: str = "protein",
    cell_name_attr: str = "cell_name",
    batch_name_attr: str = "batch",
    random_seed: int = 1234,
    include_beta: bool = False,
    dtype=torch.float64,
):
    """Create an Astir object from a loom file and a marker yaml

    :param loom_file: Path to a loom file, where rows correspond to proteins and columns to cells
    :param marker_yaml: Path to input YAML file containing marker gene information. Should include cell_type and cell_state      
        entries. See documention.
    :param protein_name_attr: The attribute (key) in the row attributes that identifies the protein names 
        (required to match with the marker gene information)
    :param cell_name_attr: The attribute (key) in the column attributes that identifies the name of each cell
    :param batch_name_attr: The attribute (key) in the column attributes that identifies the batch. A design matrix
        will be built using this (if present) using a one-hot encoding to control for batch.
    :param random_seed: The random seed to be used to initialize variables
    :param include_beta: Deprecated

    :returns: An object of class `astir.Astir` using data imported from the loom files

    .. todo:: This function is memory inefficient and goes against the philosophy of loom files. Should be improved
    """
    batch_list = None
    with loompy.connect(loom_file) as ds:
        df_gex = pd.DataFrame(ds[:, :].T)
        df_gex.columns = ds.ra[protein_name_attr]

        if cell_name_attr in ds.ca.keys():
            df_gex.index = ds.ca[cell_name_attr]

        if batch_name_attr in ds.ca.keys():
            batch_list = ds.ca[batch_name_attr]

    design = None

    if batch_list is not None:
        design = OneHotEncoder().fit_transform(batch_list.reshape(-1, 1)).todense()
        design = design[:, :-1]  # remove final column
        design = np.concatenate([np.ones((design.shape[0], 1)), design], axis=1)

    with open(marker_yaml, "r") as stream:
        marker_dict = yaml.safe_load(stream)

    return Astir(df_gex, marker_dict, design, random_seed, include_beta, dtype)


def from_anndata_yaml(
    anndata_file: str,
    marker_yaml: str,
    protein_name: str = None,
    cell_name: str = None,
    batch_name: str = "batch",
    random_seed: int = 1234,
    include_beta: bool = False,
    dtype=torch.float64,
):
    """Create an Astir object from an :class:`anndata.Anndata` file and a marker yaml

    :param anndata_file: Path to an :class:`anndata.Anndata` `h5py` file
    :param marker_yaml: Path to input YAML file containing marker gene information. Should include cell_type and cell_state      
        entries. See documention.
    :param protein_name: The column of `adata.var` containing protein names. If this is none, defaults to `adata.var_names`
    :param cell_name:  The column of `adata.obs` containing cell names. If this is none, defaults to `adata.obs_names`
    :param batch_name: The column of `adata.obs` containing batch names. A design matrix
        will be built using this (if present) using a one-hot encoding to control for batch.
    :param random_seed: The random seed to be used to initialize variables
    :param include_beta: Deprecated

    :returns: An object of class `astir.Astir` using data imported from the loom files

    .. todo:: This function is memory inefficient and goes against the philosophy of loom files. Should be improved
    """
    batch_list = None

    ad = anndata.read_h5ad(anndata_file)

    df_gex = pd.DataFrame(ad.X.toarray())

    if protein_name is not None:
        df_gex.columns = ad.var[protein_name]
    else:
        df_gex.columsn = ad.var_names

    if cell_name is not None:
        df_gex.index = ad.obs[cell_name]
    else:
        df_gex.index = ad.obs_names

    if batch_name is not None:
        batch_list = ad.obs[batch_name]

    design = None

    if batch_list is not None:
        design = (
            OneHotEncoder()
            .fit_transform(batch_list.to_numpy().reshape(-1, 1))
            .todense()
        )
        design = design[:, :-1]  # remove final column
        design = np.concatenate([np.ones((design.shape[0], 1)), design], axis=1)

    with open(marker_yaml, "r") as stream:
        marker_dict = yaml.safe_load(stream)

    return Astir(df_gex, marker_dict, design, random_seed, include_beta, dtype)
