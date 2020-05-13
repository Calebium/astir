# astir: Automated aSsignmenT sIngle-cell pRoteomics

# VSCode tips for python:
# ctrl+` to open terminal
# cmd+P to go to file

import re

import torch
from torch.autograd import Variable
from torch.distributions import Normal
import torch.nn.functional as F
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

import pandas as pd
import numpy as np

from sklearn.preprocessing import StandardScaler


class Astir:
    """ Loads a .csv expression file and a .yaml marker file.
    """

    def _param_init(self):
        """[summery]
        """
        self.initializations = {
            "mu": np.log(self.Y_np.mean(0)),
            "log_sigma": np.log(self.Y_np.std(0))
        }
        ## prior on z
        log_delta = Variable(0 * torch.ones((self.G,self.C+1)), requires_grad = True)
        self.data = {
            "log_alpha": torch.log(torch.ones(self.C+1) / (self.C+1)),
            "delta": torch.exp(log_delta),
            "rho": torch.from_numpy(self.marker_mat)
        }
        self.variables = {
            "log_sigma": Variable(torch.from_numpy(
                self.initializations["log_sigma"].copy()), requires_grad = True),
            "mu": Variable(torch.from_numpy(self.initializations["mu"].copy()), requires_grad = True),
            "log_delta": log_delta
        }

    def _sanitize_dict(self, marker_dict):
        keys = list(marker_dict.keys())
        if not len(keys) == 2:
            raise NotClassifiableError("Marker file does not follow the " +\
                "required format.")
        ct = re.compile("cell[^a-zA-Z0-9]*type", re.IGNORECASE)
        cs = re.compile("cell[^a-zA-Z0-9]*state", re.IGNORECASE)
        if ct.match(keys[0]):
            type_dict = marker_dict[keys[0]]
        elif ct.match(keys[1]):
            type_dict = marker_dict[keys[1]]
        else:
            raise NotClassifiableError("Can't find cell type dictionary" +\
                " in the marker file.")
        if cs.match(keys[0]):
            state_dict = marker_dict[keys[0]]
        elif cs.match(keys[1]):
            state_dict = marker_dict[keys[1]]
        else:
            raise NotClassifiableError("Can't find cell state dictionary" +\
                " in the marker file.")
        return type_dict, state_dict

    def _sanitize_gex(self, df_gex):
        N = df_gex.shape[0]
        G = len(self.marker_genes)
        C = len(self.cell_types)
        if N <= 0:
            raise NotClassifiableError("Classification failed. There should be " +\
                "at least one row of data to be classified.")
        if C <= 1:
            raise NotClassifiableError("Classification failed. There should be " +\
                "at least two cell types to classify the data into.")
        ## This should also be done to cell_states
        try:
            Y_np = df_gex[self.marker_genes].to_numpy()
        except(KeyError):
            raise NotClassifiableError("Classification failed. There's no " + \
                "overlap between marked genes and expression genes.")
        return N, G, C, Y_np

    def _construct_marker_mat(self):
        """[summary]

        Returns:
            [type] -- [description]
        """
        marker_mat = np.zeros(shape = (self.G,self.C+1))
        for g in range(self.G):
            for ct in range(self.C):
                gene = self.marker_genes[g]
                cell_type = self.cell_types[ct]
                if gene in self.type_dict[cell_type]:
                    marker_mat[g,ct] = 1
        # print(marker_mat)

        return marker_mat

    ## Declare pytorch forward fn
    def _forward(self, Y, X):
        """[summary]

        Arguments:
            Y {[type]} -- [description]
            X {[type]} -- [description]

        Returns:
            [type] -- [description]
        """
        
        Y_spread = Y.reshape(-1, self.G, 1).repeat(1, 1, self.C+1)
        
        mean = torch.exp(torch.exp(self.variables["log_delta"]) * self.data["rho"]\
             + self.variables["mu"].reshape(-1, 1))
        dist = Normal(mean, torch.exp(self.variables["log_sigma"]).reshape(-1, 1))
        

        log_p_y = dist.log_prob(Y_spread)

        log_p_y_on_c = log_p_y.sum(1)
        
        gamma = self.recog.forward(X)

        elbo = ( gamma * (log_p_y_on_c + self.data["log_alpha"] - torch.log(gamma)) ).sum()
        
        return -elbo

    ## Todo: an output function
    def __init__(self, df_gex, marker_dict, random_seed = 1234):
        """[summary]

        Arguments:
            df_gex {[type]} -- [description]
            marker_dict {[type]} -- [description]

        Keyword Arguments:
            random_seed {int} -- [description] (default: {1234})

        Raises:
            NotClassifiableError: [description]
        """
        #Todo: fix problem with random seed
        torch.manual_seed(random_seed)

        self.assignments = None # cell type assignment probabilities
        self.losses = None # losses after optimization

        self.type_dict, self.state_dict = self._sanitize_dict(marker_dict)

        self.cell_types = list(self.type_dict.keys())
        self.marker_genes = list(set([l for s in self.type_dict.values() for l in s]))

        self.N, self.G, self.C, self.Y_np = self._sanitize_gex(df_gex)

        # Read input data
        self.df_gex = df_gex
        self.core_names = list(df_gex.index)
        self.expression_genes = list(df_gex.columns)

        self.marker_mat = self._construct_marker_mat()

        self.dset = IMCDataSet(self.Y_np)
        self.recog = RecognitionNet(self.C, self.G)

        self._param_init()

    def fit(self, epochs = 100, 
        learning_rate = 1e-2, batch_size = 1024):
        """[summary]

        Keyword Arguments:
            epochs {int} -- [description] (default: {100})
            learning_rate {[type]} -- [description] (default: {1e-2})
            batch_size {int} -- [description] (default: {1024})
        """
        ## Make dataloader
        dataloader = DataLoader(self.dset, batch_size=min(batch_size, self.N), shuffle=True)

        ## Run training loop
        losses = np.empty(epochs)

        ## Construct optimizer
        optimizer = torch.optim.Adam(list(self.variables.values()) + list(self.recog.parameters()),\
            lr=learning_rate)

        for ep in range(epochs):
            L = None
            
            for batch in dataloader:
                Y,X = batch
                optimizer.zero_grad()
                L = self._forward(Y, X)
                L.backward()
                optimizer.step()
            
            l = L.detach().numpy()
            losses[ep] = l
            print(l)

        ## Save output
        g = self.recog.forward(self.dset.X).detach().numpy()

        self.assignments = pd.DataFrame(g)
        self.assignments.columns = self.cell_types + ['Other']
        self.assignments.index = self.core_names

        self.losses = losses

        print("Done!")

    def get_assignments(self):
        """[summary]

        Returns:
            [type] -- [description]
        """
        return self.assignments
    
    def get_losses(self):
        """[summary]

        Returns:
            [type] -- [description]
        """
        return self.losses

    def to_csv(self, output_csv):
        """[summary]

        Arguments:
            output_csv {[type]} -- [description]
        """
        self.assignments.to_csv(output_csv)

    def __str__(self):
        return "Astir object with " + str(self.Y_np.shape[0]) + " rows and " + \
            str(self.Y_np.shape[1]) + " columns of data, " + \
            str(len(self.cell_types)) + " types of possible cell assignment" 


class NotClassifiableError(RuntimeError):
    pass

## Dataset class: for loading IMC datasets
class IMCDataSet(Dataset):
    
    def __init__(self, Y_np):
             
        self.Y = torch.from_numpy(Y_np)
        X = StandardScaler().fit_transform(Y_np)
        self.X = torch.from_numpy(X)
    
    def __len__(self):
        return self.Y.shape[0]
    
    def __getitem__(self, idx):
        return self.Y[idx,:], self.X[idx,:]

## Recognition network
class RecognitionNet(nn.Module):
    def __init__(self, C, G, h=6):
        super(RecognitionNet, self).__init__()
        self.hidden_1 = nn.Linear(G, h).double()
        self.hidden_2 = nn.Linear(h, C+1).double()

    def forward(self, x):
        x = self.hidden_1(x)
        x = F.relu(x)
        x = self.hidden_2(x)
        x = F.softmax(x, dim=1)
        return x

