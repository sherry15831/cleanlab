
# coding: utf-8

# ## The RankPruning algorithm class for multiclass learning with noisy labels. 
# #### The RankPruning class wraps around an instantion of a classifier. Your classifier must adhere to the sklearn template, meaning it must define three functions:
# * clf.fit(X, y, sample_weight = None)
# * clf.predict_proba(X)
# * clf.predict(X)
# 
# where 'X' (of length n) contains your data, 'y' (of length n) contains your targets formatted as 0, 1, 2, ..., K-1, and sample_weight (of length n) that reweights examples in the loss function while training.
# 
# ## Example
# 
# ```python
# from confidentlearning.classification import RankPruning
# from sklearn.linear_model import LogisticRegression as logreg
# rp = RankPruning(clf = logreg())
# rp.fit(X_train, y_train_noisy)
# y_pred = rp.predict(X_test) # Predictions approximate rp.fit(X_train, y_train_no_noise)
# ```
# ## Notes
# 
# * s - used to denote the noisy labels in the code
# * Class labels (K classes) must be formatted as natural numbers: 0, 1, 2, ..., K-1
# * Do not skip a natural number, i.e. 0, 1, 3, 4, .. is ***NOT*** okay!

# In[1]:


from __future__ import print_function

from sklearn.linear_model import LogisticRegression as logreg
from sklearn.model_selection import StratifiedKFold
import numpy as np

from confidentlearning.util import assert_inputs_are_valid, value_counts, remove_noise_from_class
from confidentlearning.latent_estimation import     estimate_py_noise_matrices_and_cv_pred_proba,     estimate_py_and_noise_matrices_from_probabilities,     estimate_cv_predicted_probabilities
from confidentlearning.latent_algebra import compute_py_inv_noise_matrix, compute_noise_matrix_from_inverse
from confidentlearning.pruning import get_noise_indices


# In[292]:


class RankPruning(object):
    '''Rank Pruning is a state-of-the-art algorithm (2017) for 
      multiclass classification with (potentially extreme) mislabeling 
      across any or all pairs of class labels. It works with ANY classifier,
      including deep neural networks. See clf parameter.
    This subfield of machine learning is referred to as Confident Learning.
    Rank Pruning also achieves state-of-the-art performance for binary
      classification with noisy labels and positive-unlabeled
      learning (PU learning) where a subset of positive examples is given and
      all other examples are unlabeled and assumed to be negative examples.
    Rank Pruning works by "learning from confident examples." Confident examples are
      identified as examples with high predicted probability for their training label.
    Given any classifier having the predict_proba() method, an input feature matrix, X, 
      and a discrete vector of labels, s, which may contain mislabeling, Rank Pruning 
      estimates the classifications that would be obtained if the hidden, true labels, y,
      had instead been provided to the classifier during training.
    "s" denotes the noisy label instead of \tilde(y), for ASCII encoding reasons.

    Parameters 
    ----------
    clf : sklearn.classifier or equivalent class
      The clf object must have the following three functions defined:
        1. clf.predict_proba(X) # Predicted probabilities
        2. clf.predict(X) # Predict labels
        3. clf.fit(X,y) # Train classifier
      Stores the classifier used in Rank Pruning.
      Default classifier used is logistic regression.
        
    seed : int (default = None)
        Number to set the default state of the random number generator used to split 
        the cross-validated folds. If None, uses np.random current random state.'''  
  
  
    def __init__(self, clf = None, seed = None):
        self.clf = logreg() if clf is None else clf
        self.seed = seed
        if seed is not None:
            np.random.seed(seed = seed)
  
  
    def fit(
        self, 
        X,
        s,
        cv_n_folds = 5,
        pulearning = None,
        psx = None,
        thresholds = None,
        noise_matrix = None,
        inverse_noise_matrix = None,
        prune_method = 'prune_by_noise_rate',
        prune_count_method = 'inverse_nm_dot_s',
        converge_latent_estimates = False,
        
    ):
        '''This method implements the Rank Pruning mantra 'learning with confident examples.'
        This function fits the classifer (self.clf) to (X, s) accounting for the noise in
        both the positive and negative sets.

        Parameters
        ----------
        X : np.array
          Input feature matrix (N, D), 2D numpy array

        s : np.array
          A binary vector of labels, s, which may contain mislabeling. "s" denotes
          the noisy label instead of \tilde(y), for ASCII encoding reasons.

        cv_n_folds : int
          The number of cross-validation folds used to compute
          out-of-sample probabilities for each example in X.

        pulearning : int
          Set to the integer of the class that is perfectly labeled, if such
          a class exists. Otherwise, or if you are unsure, 
          leave pulearning = None (default).

        psx : np.array (shape (N, K))
          P(s=k|x) is a matrix with K (noisy) probabilities for each of the N examples x.
          This is the probability distribution over all K classes, for each
          example, regarding whether the example has label s==k P(s=k|x). psx should
          have been computed using 3 (or higher) fold cross-validation.
          If you are not sure, leave psx = None (default) and
          it will be computed for you using cross-validation.

        thresholds : iterable (list or np.array) of shape (K, 1)  or (K,)
          P(s^=k|s=k). If an example has a predicted probability "greater" than 
          this threshold, it is counted as having hidden label y = k. This is 
          not used for pruning, only for estimating the noise rates using 
          confident counts. This value should be between 0 and 1. Default is None.

        noise_matrix : np.array of shape (K, K), K = number of classes 
          A conditional probablity matrix of the form P(s=k_s|y=k_y) containing
          the fraction of examples in every class, labeled as every other class.
          Assumes columns of noise_matrix sum to 1. 
    
        inverse_noise_matrix : np.array of shape (K, K), K = number of classes 
          A conditional probablity matrix of the form P(y=k_y|s=k_s) representing
          the estimated fraction observed examples in each class k_s, that are
          mislabeled examples from every other class k_y. If None, the 
          inverse_noise_matrix will be computed from psx and s.
          Assumes columns of inverse_noise_matrix sum to 1.

        prune_method : str
          'prune_by_class', 'prune_by_noise_rate', or 'both'. Method used for pruning.
          
        prune_count_method : str (default 'inverse_nm_dot_s')
          Options are 'inverse_nm_dot_s' or 'calibrate_confident_joint'. Method used to estimate the counts of the
          joint P(s, y) that will be used to determine which how many examples to prune
          for every class that are flipped to every other class.

        converge_latent_estimates : bool (Default: False)
          If true, forces numerical consistency of estimates. Each is estimated
          independently, but they are related mathematically with closed form 
          equivalences. This will iteratively enforce mathematically consistency.

        Output
        ------
          Returns (noise_mask, sample_weight)'''
    
        # Check inputs
        assert_inputs_are_valid(X, s, psx)
        if noise_matrix is not None and np.trace(noise_matrix) <= 1:
            raise Exception("Trace(noise_matrix) must exceed 1.")
        if inverse_noise_matrix is not None and np.trace(inverse_noise_matrix) <= 1:
            raise Exception("Trace(inverse_noise_matrix) must exceed 1.")

        # Number of classes
        self.K = len(np.unique(s))

        # 'ps' is p(s=k)
        self.ps = value_counts(s) / float(len(s))

        self.confident_joint = None
        # If needed, compute noise rates (fraction of mislabeling) for all classes. 
        # Also, if needed, compute P(s=k|x), denoted psx.
        
        if noise_matrix is not None:
            self.noise_matrix = noise_matrix
            if inverse_noise_matrix is None:
                self.py, self.inverse_noise_matrix = compute_py_inv_noise_matrix(self.ps, self.noise_matrix)
        if inverse_noise_matrix is not None:
            self.inverse_noise_matrix = inverse_noise_matrix
            if noise_matrix is None:
                self.noise_matrix = compute_noise_matrix_from_inverse(self.ps, self.inverse_noise_matrix)
        if noise_matrix is None and inverse_noise_matrix is None:
            if psx is None:
                self.py, self.noise_matrix, self.inverse_noise_matrix, self.confident_joint, psx =                 estimate_py_noise_matrices_and_cv_pred_proba(
                    X = X, 
                    s = s, 
                    clf = self.clf,
                    cv_n_folds = cv_n_folds,
                    thresholds = thresholds, 
                    converge_latent_estimates = converge_latent_estimates,
                    seed = self.seed,
                )
            else: # psx is provided by user (assumed holdout probabilities)
                self.py, self.noise_matrix, self.inverse_noise_matrix, self.confident_joint =                 estimate_py_and_noise_matrices_from_probabilities(
                    s = s, 
                    psx = psx,
                    thresholds = thresholds, 
                    converge_latent_estimates = converge_latent_estimates,
                )

        if psx is None: 
            psx = estimate_cv_predicted_probabilities(
                X = X, 
                labels = s, 
                clf = self.clf,
                cv_n_folds = cv_n_folds,
                seed = self.seed,
            ) 

        # Zero out noise matrix entries if pulearning = the integer specifying the class without noise.
        if pulearning is not None:
            self.noise_matrix = remove_noise_from_class(self.noise_matrix, class_without_noise=pulearning)
            # TODO: self.inverse_noise_matrix = remove_noise_from_class(self.inverse_noise_matrix, class_without_noise=pulearning)

        # This is the actual work of this function.

        # Get the indices of the examples we wish to prune
        self.noise_mask = get_noise_indices(
            s, 
            psx, 
            inverse_noise_matrix = self.inverse_noise_matrix, 
            confident_joint = self.confident_joint,
            prune_method = prune_method, 
            prune_count_method = prune_count_method,
            converge_latent_estimates = converge_latent_estimates,
        ) 

        X_mask = ~self.noise_mask
        X_pruned = X[X_mask]
        s_pruned = s[X_mask]

        # Re-weight examples in the loss function for the final fitting
        # s.t. the "apparent" original number of examples in each class
        # is preserved, even though the pruned sets may differ.
        self.sample_weight = np.ones(np.shape(s_pruned))
        for k in range(self.K): 
            self.sample_weight[s_pruned == k] = 1.0 / self.noise_matrix[k][k]

        self.clf.fit(X_pruned, s_pruned, sample_weight=self.sample_weight)
        return self.clf
    
    
    def predict(self, X):
        '''Returns a binary vector of predictions.'''

        return self.clf.predict(X)
  
  
    def predict_proba(self, X):
        '''Returns a vector of probabilties P(y=k)
        for each example in X.'''

        return self.clf.predict_proba(X)
