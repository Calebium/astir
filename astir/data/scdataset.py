import warnings
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, Dataset

# from sklearn.cluster import SpectralClustering

import scanpy as sc
from sklearn.preprocessing import StandardScaler


class SCDataset(Dataset):
    """Container for single-cell proteomic data in the form of
    a pytorch dataset

    :param expr_input: Input expression data. See details :`expr_input` is either a `pd.DataFrame`
        or a three-element `tuple`. When it is `pd.DataFrame`, its index and column should indicate the cell
        name and feature name of the dataset; when it is a three-element `tuple`, it should be in the form
        of `Tuple[Union[np.array, torch.Tensor], List[str], List[str]]` and  its first element should
        be the actual dataset as either `np.array` or `torch.tensor`, the second element should be
        a list containing the name of the columns or the names of features, the third element should be a
        list containing the name of the indices or the names of the cells.:
    :param marker_dict: Marker dictionary containing cell type and
        information. See details :The dictionary maps the name of cell type/state to protein features. :
    :param design: A design matrix
    :param include_other_column: Should an additional 'other' column be included?
    :param dtype: torch datatype of the model
    """

    def __init__(
        self,
        expr_input: Union[
            pd.DataFrame, Tuple[Union[np.array, torch.Tensor], List[str], List[str]]
        ],
        marker_dict: Dict[str, List[str]],
        include_other_column: bool,
        n_other: int = 1,
        design: Optional[Union[np.array, pd.DataFrame]] = None,
        dtype: torch.dtype = torch.float64,
        device: torch.device = torch.device("cpu"),
    ) -> None:
        """Initialize an SCDataset object."""
        self._dtype = dtype
        self._marker_dict = marker_dict
        self._m_features = sorted(
            list(set([l for s in marker_dict.values() for l in s]))
        )
        self._classes = list(marker_dict.keys())

        self._n_other = n_other

        self._device = device
        ## sanitize features
        if len(self._classes) <= 1 and include_other_column:
            raise NotClassifiableError(
                "Classification failed. There "
                + "should be at least two cell classes to classify the data into."
            )
        self._marker_mat = self._construct_marker_mat(
            include_other_column=include_other_column,
            n_other = n_other
        )

        if isinstance(expr_input, pd.DataFrame):
            self._exprs = self._process_df_input(expr_input)
            self._expr_features = list(expr_input.columns)
            self._cell_names = list(expr_input.index)
        elif isinstance(expr_input, tuple):
            self._expr_features = expr_input[1]
            self._cell_names = expr_input[2]
            self._exprs = self._process_tp_input(expr_input[0])
        self._design = self._fix_design(design)
        ## sanitize df
        if self._exprs.shape[0] <= 0:
            raise NotClassifiableError(
                "Classification failed. There "
                + "should be at least one row of data to be classified."
            )
        self._exprs_mean = self._exprs.mean(0)
        self._exprs_std = self._exprs.std(0)

        self.normalize()


    def get_delta_mu_init2(self, nc=None, method="scanpy"):
        exprs = self._exprs.detach().numpy()
        qs = np.quantile(exprs, q = [0.5, 0.99], axis=0)
        mean_init = qs[0,:]
        ldi = qs[1,:] / mean_init
        ldi = np.log(np.log(ldi))

        # print(mean_init)
        # print(ldi)

        return np.log(mean_init), ldi

    def get_delta_mu_init(self, nc=None, method="scanpy"):
        print("Initializing from delta")


        G = self.get_n_features()
        C = self.get_n_classes()

        nc = 2 * C

        exprs = self._exprs.detach().numpy()

        exprs_scaled = StandardScaler().fit_transform(exprs)

        if method == "scanpy":
            adata = sc.AnnData(exprs)
            sc.tl.pca(adata, svd_solver='arpack')
            sc.pp.neighbors(adata, n_neighbors=50)
            sc.tl.leiden(adata)
            clusters = adata.obs['leiden']
            clusters = clusters.astype("int64").to_numpy()
        else:

            scl = SpectralClustering(n_clusters=nc, n_init=100,
                            assign_labels='discretize')
            clusters = scl.fit_predict(exprs_scaled) 

        s = []
        for cluster in np.unique(clusters):
            if (cluster == clusters).sum() > 0:
                s.append(exprs[clusters == cluster,:].mean(0))
        s = np.array(s)

        # feature_max = s.max(0)
        # feature_min = s.min(0)
        qs = np.quantile(s, q = [0.1, 0.9], axis=0)
        feature_min = qs[0,:]
        feature_max = qs[1,:]

        delta_init = (feature_max / feature_min)

        log_delta_init = np.log(np.log(delta_init))
        log_delta_init[np.isinf(log_delta_init)] = log_delta_init[np.isinf(log_delta_init) == False].max()


        
        log_delta_init = np.repeat(log_delta_init.reshape((G, 1)), C + self._n_other, axis=1)

        print("Done")

        return log_delta_init, np.log(feature_min)



    def _process_df_input(self, df_input: pd.DataFrame) -> torch.Tensor:
        """Processes input as pd.DataFrame and convert it into torch.Tensor

        :param df_input: the input
        :raises NotClassifiableError: raised when there is no overlap between the
            data and the marker
        :return: the processed input as a torch.Tensor
        """
        try:
            Y_np = df_input[self._m_features].values
            return torch.from_numpy(Y_np).to(device=self._device, dtype=self._dtype)
        except (KeyError):
            raise NotClassifiableError(
                "Classification failed. There's no "
                + "overlap between marked features and expression features for "
                + "the classification of cell type/state."
            )

    def _process_tp_input(self, in_data: Union[torch.Tensor, np.array]) -> torch.Tensor:
        """Process the input as Tuple[np.array, np.array, np.array] and convert it
            to torch.Tensor.

        :param in_data: input as a np.array or torch.tensor
        :raises NotClassifiableError: raised when there is no overlap between marked
            features and expression feature.
        :return: the processed input as a torch.Tensor
        """
        ind = [
            self._expr_features.index(name)
            for name in self._m_features
            if name in self._expr_features
        ]
        if len(ind) <= 0:
            raise NotClassifiableError(
                "Classification failed. There's no "
                + "overlap between marked features and expression features for "
                + "the classification of cell type/state."
            )
        if len(ind) < len(self._m_features):
            warnings.warn("Classified features are less than marked features.")
        Y_np = in_data[:, ind]
        if torch.is_tensor(Y_np):
            return Y_np.to(device=self._device, dtype=self._dtype)
        return torch.from_numpy(Y_np).to(device=self._device, dtype=self._dtype)

    def _construct_marker_mat(self, 
        include_other_column: bool,
        n_other: int=1) -> torch.Tensor:
        """Construct a marker matrix.

        :param include_other_column: indicates whether or not include other columns.
        :return: A marker matrix. The rows are features and the coloumns are
            the corresponding classes (type/state).
        """
        G = self.get_n_features()
        C = self.get_n_classes()

        marker_mat = 0 * torch.ones(
            (G, C + n_other if include_other_column else C), dtype=self._dtype
        ).to(self._device)
        for g, feature in enumerate(self._m_features):
            for c, cell_class in enumerate(self._classes):
                if feature in self._marker_dict[cell_class]:
                    marker_mat[g, c] = 1.0
                # else:
                #     marker_mat[g,c] = 0.0
        return marker_mat

    def __len__(self) -> int:
        """Length of the input file

        :return: total number of cells
        """
        # N
        return self._exprs.shape[0]

    def __getitem__(
        self, idx: Union[slice, int]
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Returns the protein expression of the indexed cell on the SCDataset
        object

        :param idx: the index of the cell
        :return: raw protein expression, normalized protein expression,
        sanitized design matrix of the cell at index
        """
        y = self._exprs[idx, :]
        x = (y - self._exprs_mean) / self._exprs_std
        return y, x, self._design[idx, :]

    def _fix_design(self, design: Union[np.array, pd.DataFrame]) -> torch.Tensor:
        """Sanitize the design matrix.

        :param design: the unsanitized design matrix
        :raises NotClassifiableError: raised when the design matrix has
            different number of rows from the expression data
        :return: the sanitized design matrix
        """
        d = None
        if design is None:
            d = torch.ones((self._exprs.shape[0], 1)).to(
                device=self._device, dtype=self._dtype
            )
        else:
            if isinstance(design, pd.DataFrame):
                design = design.values
            d = torch.from_numpy(design).to(device=self._device, dtype=self._dtype)

        if d.shape[0] != self._exprs.shape[0]:
            raise NotClassifiableError(
                "Number of rows of design matrix "
                + "must equal number of rows of expression data"
            )
        return d

    def rescale(self) -> None:
        """Normalize the expression data."""
        self._exprs = self._exprs / (self.get_sigma())

    def get_dtype(self) -> torch.dtype:
        """Get the dtype of the `SCDataset`.

        :return: `self._dtype`
        :rtype: torch.dtype
        """
        return self._dtype

    def get_exprs(self) -> torch.Tensor:
        """Return the expression data as a :class:`torch.Tensor`."""
        return self._exprs

    def get_exprs_df(self) -> pd.DataFrame:
        """Return the expression data as a :class:`pandas.DataFrame`."""
        df = pd.DataFrame(self._exprs.detach().cpu().numpy())
        df.index = self.get_cell_names()
        df.columns = self.get_features()
        return df

    def get_marker_mat(self) -> torch.Tensor:
        """Return the marker matrix as a :class:`torch.Tensor`."""
        return self._marker_mat

    def get_mu(self) -> torch.Tensor:
        """Get the mean expression of each protein as a :class:`torch.Tensor`."""
        return self._exprs_mean

    def get_sigma(self) -> torch.Tensor:
        """Get the standard deviation of each protein

        :return: standard deviation of each protein
        """
        return self._exprs_std

    def get_n_classes(self) -> int:
        """Get the number of 'classes': either the number of cell types or cell states."""
        return len(self._classes)

    def get_n_cells(self) -> int:
        """Get the number of cells: either the number of cell types or cell states."""
        return len(self.get_cell_names())

    def get_n_features(self) -> int:
        """Get the number of features (proteins)."""
        return len(self._m_features)

    def get_features(self) -> List[str]:
        """Get the features (proteins).

        :return: return self._m_features
        :rtype: List[str]
        """
        return self._m_features

    def get_cell_names(self) -> List[str]:
        """Get the cell names.

        :return: return self._cell_names
        :rtype: List[str]
        """
        return self._cell_names

    def get_classes(self) -> List[str]:
        """Get the cell types/states.

        :return: return self._classes
        :rtype: List[str]
        """
        return self._classes

    def get_design(self) -> torch.Tensor:
        """Get the design matrix.

        :return: return self._design
        :rtype: torch.Tensor
        """
        return self._design

    def normalize(
        self,
        percentile_lower: float = 0,
        percentile_upper: float = 99.9,
        cofactor: float = 5.0,
    ) -> None:
        """Normalize the expression data

        This performs a two-step normalization:
        1. A `log(1+x)` transformation to the data
        2. Winsorizes to (`percentile_lower`, `percentile_upper`)

        :param percentile_lower: the lower bound percentile for
            winsorization, defaults to 0
        :param percentil_upper: the upper bound percentile for winsorization,
            defaults to 99.9
        :param cofactor: a cofactor constant, defaults to 5.0
        """

        with torch.no_grad():
            exprs = self.get_exprs().cpu().numpy()
            # exprs = np.arcsinh(exprs / cofactor)
            q_low = np.percentile(exprs, (percentile_lower), axis=0)
            q_high = np.percentile(exprs, (percentile_upper), axis=0)

            for g in range(exprs.shape[1]):
                exprs[:, g][exprs[:, g] < q_low[g]] = q_low[g]
                exprs[:, g][exprs[:, g] > q_high[g]] = q_high[g]

            self._exprs = torch.tensor(exprs)


class NotClassifiableError(RuntimeError):
    pass
