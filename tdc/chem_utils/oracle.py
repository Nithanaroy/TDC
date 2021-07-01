import pickle 
import numpy as np 
import re
import os.path as op
import math
from collections import defaultdict, Iterable
from abc import abstractmethod
from functools import partial
from typing import List
import time
import os 

try:
	from sklearn import svm
	# from sklearn.metrics import roc_auc_score, f1_score, average_precision_score, precision_score, recall_score, accuracy_score
except:
	ImportError("Please install sklearn by 'conda install -c anaconda scikit-learn' or 'pip install scikit-learn '! ")

try: 
  import rdkit
  from rdkit import Chem, DataStructs
  from rdkit.Chem import AllChem
  from rdkit.Chem import Descriptors
  import rdkit.Chem.QED as QED
  from rdkit import rdBase
  rdBase.DisableLog('rdApp.error')
  from rdkit.Chem import rdMolDescriptors
  from rdkit.six.moves import cPickle
  from rdkit.six import iteritems
  from rdkit.Chem.Fingerprints import FingerprintMols
  from rdkit.Chem import MACCSkeys
except:
  raise ImportError("Please install rdkit by 'conda install -c conda-forge rdkit'! ")	

try:
	from scipy.stats.mstats import gmean
except:
	raise ImportError("Please install rdkit by 'pip install scipy'! ") 


try:
	import networkx as nx 
except:
	raise ImportError("Please install networkx by 'pip install networkx'! ")	

from ..utils import oracle_load, print_sys, install

mean2func = {
  'geometric': gmean, 
  'arithmetic': np.mean, 
}


def smiles_to_rdkit_mol(smiles):
  """Convert smiles into rdkit's mol (molecule) format. 

  Args: 
    smiles: str

  Returns:
    mol: rdkit.Chem.rdchem.Mol

  """
  mol = Chem.MolFromSmiles(smiles)
  #  Sanitization check (detects invalid valence)
  if mol is not None:
    try:
      Chem.SanitizeMol(mol)
    except ValueError:
      return None
  return mol

def smiles_2_fingerprint_ECFP4(smiles):
  """Convert smiles into ECFP4 Morgan Fingerprint. 

  Args: 
    smiles: str

  Returns:
    fp: rdkit.DataStructs.cDataStructs.UIntSparseIntVect

  """
  molecule = smiles_to_rdkit_mol(smiles)
  fp = AllChem.GetMorganFingerprint(molecule, 2)
  return fp 


def smiles_2_fingerprint_FCFP4(smiles):
  """Convert smiles into FCFP4 Morgan Fingerprint. 

  Args: 
    smiles: str

  Returns:
    fp: rdkit.DataStructs.cDataStructs.UIntSparseIntVect

  """
  molecule = smiles_to_rdkit_mol(smiles)
  fp = AllChem.GetMorganFingerprint(molecule, 2, useFeatures=True)
  return fp 


def smiles_2_fingerprint_AP(smiles):
  """Convert smiles into Atom Pair Fingerprint. 

  Args: 
    smiles: str

  Returns:
    fp: rdkit.DataStructs.cDataStructs.IntSparseIntVect

  """
  molecule = smiles_to_rdkit_mol(smiles)
  fp = AllChem.GetAtomPairFingerprint(molecule, maxLength=10)
  return fp 

def smiles_2_fingerprint_ECFP6(smiles):
  """Convert smiles into ECFP6 Fingerprint. 

  Args: 
    smiles: str

  Returns:
    fp: rdkit.DataStructs.cDataStructs.UIntSparseIntVect

  """  
  molecule = smiles_to_rdkit_mol(smiles)
  fp = AllChem.GetMorganFingerprint(molecule, 3)
  return fp 

fp2fpfunc = {'ECFP4': smiles_2_fingerprint_ECFP4, 
             'FCFP4': smiles_2_fingerprint_FCFP4, 
             'AP': smiles_2_fingerprint_AP, 
             'ECFP6': smiles_2_fingerprint_ECFP6
}


class ScoreModifier:
    """
    Interface for score modifiers.
    """

    @abstractmethod
    def __call__(self, x):
        """
        Apply the modifier on x.

        Args:
            x: float or np.array to modify

        Returns:
            float or np.array (depending on the type of x) after application of the distance function.
        """


class ChainedModifier(ScoreModifier):
    """
    Calls several modifiers one after the other, for instance:
        score = modifier3(modifier2(modifier1(raw_score)))
    """

    def __init__(self, modifiers: List[ScoreModifier]) -> None:
        """
        Args:
            modifiers: modifiers to call in sequence.
                The modifier applied last (and delivering the final score) is the last one in the list.
        """
        self.modifiers = modifiers

    def __call__(self, x):
        score = x
        for modifier in self.modifiers:
            score = modifier(score)
        return score


class LinearModifier(ScoreModifier):
    """
    Score modifier that multiplies the score by a scalar (default: 1, i.e. do nothing).
    """

    def __init__(self, slope=1.0):
        self.slope = slope

    def __call__(self, x):
        return self.slope * x


class SquaredModifier(ScoreModifier):
    """
    Score modifier that has a maximum at a given target value, and decreases
    quadratically with increasing distance from the target value.
    """

    def __init__(self, target_value: float, coefficient=1.0) -> None:
        self.target_value = target_value
        self.coefficient = coefficient

    def __call__(self, x):
        return 1.0 - self.coefficient * np.square(self.target_value - x)


class AbsoluteScoreModifier(ScoreModifier):
    """
    Score modifier that has a maximum at a given target value, and decreases
    linearly with increasing distance from the target value.
    """

    def __init__(self, target_value: float) -> None:
        self.target_value = target_value

    def __call__(self, x):
        return 1. - np.abs(self.target_value - x)


class GaussianModifier(ScoreModifier):
    """
    Score modifier that reproduces a Gaussian bell shape.
    """

    def __init__(self, mu: float, sigma: float) -> None:
        self.mu = mu
        self.sigma = sigma

    def __call__(self, x):
        return np.exp(-0.5 * np.power((x - self.mu) / self.sigma, 2.))


class MinMaxGaussianModifier(ScoreModifier):
    """
    Score modifier that reproduces a half Gaussian bell shape.
    For minimize==True, the function is 1.0 for x <= mu and decreases to zero for x > mu.
    For minimize==False, the function is 1.0 for x >= mu and decreases to zero for x < mu.
    """

    def __init__(self, mu: float, sigma: float, minimize=False) -> None:
        self.mu = mu
        self.sigma = sigma
        self.minimize = minimize
        self._full_gaussian = GaussianModifier(mu=mu, sigma=sigma)

    def __call__(self, x):
        if self.minimize:
            mod_x = np.maximum(x, self.mu)
        else:
            mod_x = np.minimum(x, self.mu)
        return self._full_gaussian(mod_x)


MinGaussianModifier = partial(MinMaxGaussianModifier, minimize=True)
MaxGaussianModifier = partial(MinMaxGaussianModifier, minimize=False)


class ClippedScoreModifier(ScoreModifier):
    r"""
    Clips a score between specified low and high scores, and does a linear interpolation in between.

    This class works as follows:
    First the input is mapped onto a linear interpolation between both specified points.
    Then the generated values are clipped between low and high scores.
    """

    def __init__(self, upper_x: float, lower_x=0.0, high_score=1.0, low_score=0.0) -> None:
        """
        Args:
            upper_x: x-value from which (or until which if smaller than lower_x) the score is maximal
            lower_x: x-value until which (or from which if larger than upper_x) the score is minimal
            high_score: maximal score to clip to
            low_score: minimal score to clip to
        """
        assert low_score < high_score

        self.upper_x = upper_x
        self.lower_x = lower_x
        self.high_score = high_score
        self.low_score = low_score

        self.slope = (high_score - low_score) / (upper_x - lower_x)
        self.intercept = high_score - self.slope * upper_x

    def __call__(self, x):
        y = self.slope * x + self.intercept
        return np.clip(y, self.low_score, self.high_score)


class SmoothClippedScoreModifier(ScoreModifier):
    """
    Smooth variant of ClippedScoreModifier.

    Implemented as a logistic function that has the same steepness as ClippedScoreModifier in the
    center of the logistic function.
    """

    def __init__(self, upper_x: float, lower_x=0.0, high_score=1.0, low_score=0.0) -> None:
        """
        Args:
            upper_x: x-value from which (or until which if smaller than lower_x) the score approaches high_score
            lower_x: x-value until which (or from which if larger than upper_x) the score approaches low_score
            high_score: maximal score (reached at +/- infinity)
            low_score: minimal score (reached at -/+ infinity)
        """
        assert low_score < high_score

        self.upper_x = upper_x
        self.lower_x = lower_x
        self.high_score = high_score
        self.low_score = low_score

        # Slope of a standard logistic function in the middle is 0.25 -> rescale k accordingly
        self.k = 4.0 / (upper_x - lower_x)
        self.middle_x = (upper_x + lower_x) / 2
        self.L = high_score - low_score

    def __call__(self, x):
        return self.low_score + self.L / (1 + np.exp(-self.k * (x - self.middle_x)))


class ThresholdedLinearModifier(ScoreModifier):
    """
    Returns a value of min(input, threshold)/threshold.
    """

    def __init__(self, threshold: float) -> None:
        self.threshold = threshold

    def __call__(self, x):
        return np.minimum(x, self.threshold) / self.threshold

# check the license for the code from readFragmentScores to CalculateScore here: https://github.com/EricTing/SAscore/blob/89d7689a85efed3cc918fb8ba6fe5cedf60b4a5a/src/sascorer.py#L134
_fscores = None
def readFragmentScores(name='fpscores'):
    import gzip
    global _fscores
    # generate the full path filename:
    # if name == "fpscores":
    #     name = op.join(previous_directory(op.dirname(__file__)), name)
    name = oracle_load('fpscores')
    try:
      with open('oracle/fpscores.pkl', "rb") as f:
        _fscores = pickle.load(f)
    except EOFError:
      import sys
      sys.exit("TDC is hosted in Harvard Dataverse and it is currently under maintenance, please check back in a few hours or checkout https://dataverse.harvard.edu/.")

    outDict = {}
    for i in _fscores:
        for j in range(1,len(i)):
            outDict[i[j]] = float(i[0])
    _fscores = outDict

def numBridgeheadsAndSpiro(mol,ri=None):
  nSpiro = rdMolDescriptors.CalcNumSpiroAtoms(mol)
  nBridgehead = rdMolDescriptors.CalcNumBridgeheadAtoms(mol)
  return nBridgehead,nSpiro

def calculateScore(m):
  if _fscores is None: readFragmentScores()

  # fragment score
  fp = rdMolDescriptors.GetMorganFingerprint(m,2)  #<- 2 is the *radius* of the circular fingerprint
  fps = fp.GetNonzeroElements()
  score1 = 0.
  nf = 0
  for bitId,v in iteritems(fps):
    nf += v
    sfp = bitId
    score1 += _fscores.get(sfp,-4)*v
  score1 /= nf

  # features score
  nAtoms = m.GetNumAtoms()
  nChiralCenters = len(Chem.FindMolChiralCenters(m,includeUnassigned=True))
  ri = m.GetRingInfo()
  nBridgeheads,nSpiro=numBridgeheadsAndSpiro(m,ri)
  nMacrocycles=0
  for x in ri.AtomRings():
    if len(x)>8: nMacrocycles+=1

  sizePenalty = nAtoms**1.005 - nAtoms
  stereoPenalty = math.log10(nChiralCenters+1)
  spiroPenalty = math.log10(nSpiro+1)
  bridgePenalty = math.log10(nBridgeheads+1)
  macrocyclePenalty = 0.
  # ---------------------------------------
  # This differs from the paper, which defines:
  #  macrocyclePenalty = math.log10(nMacrocycles+1)
  # This form generates better results when 2 or more macrocycles are present
  if nMacrocycles > 0: macrocyclePenalty = math.log10(2)

  score2 = 0. -sizePenalty -stereoPenalty -spiroPenalty -bridgePenalty -macrocyclePenalty

  # correction for the fingerprint density
  # not in the original publication, added in version 1.1
  # to make highly symmetrical molecules easier to synthetise
  score3 = 0.
  if nAtoms > len(fps):
    score3 = math.log(float(nAtoms) / len(fps)) * .5

  sascore = score1 + score2 + score3

  # need to transform "raw" value into scale between 1 and 10
  min = -4.0
  max = 2.5
  sascore = 11. - (sascore - min + 1) / (max - min) * 9.
  # smooth the 10-end
  if sascore > 8.: sascore = 8. + math.log(sascore+1.-9.)
  if sascore > 10.: sascore = 10.0
  elif sascore < 1.: sascore = 1.0 

  return sascore

"""Scores based on an ECFP classifier for activity."""

# clf_model = None
def load_drd2_model():
    name = 'oracle/drd2.pkl'
    try:
      with open(name, "rb") as f:
          clf_model = pickle.load(f)
    except EOFError:
      import sys
      sys.exit("TDC is hosted in Harvard Dataverse and it is currently under maintenance, please check back in a few hours or checkout https://dataverse.harvard.edu/.")

    return clf_model

def fingerprints_from_mol(mol):
    fp = AllChem.GetMorganFingerprint(mol, 3, useCounts=True, useFeatures=True)
    size = 2048
    nfp = np.zeros((1, size), np.int32)
    for idx,v in fp.GetNonzeroElements().items():
        nidx = idx%size
        nfp[0, nidx] += int(v)
    return nfp

def drd2(smile):
    """Evaluate DRD2 score of a SMILES string

    Args:
      smiles: str

    Returns:
      drd_score: float 

    """

    if 'drd2_model' not in globals().keys():
        global drd2_model
        drd2_model = load_drd2_model() 

    mol = Chem.MolFromSmiles(smile)
    if mol:
        fp = fingerprints_from_mol(mol)
        score = drd2_model.predict_proba(fp)[:, 1]
        drd_score = float(score)
        return drd_score
    return 0.0

def load_cyp3a4_veith():
  oracle_file = "oracle/cyp3a4_veith.pkl"
  try:
    with open(oracle_file, "rb") as f:
      cyp3a4_veith_model = pickle.load(f)
  except EOFError:
    import sys
    sys.exit("TDC is hosted in Harvard Dataverse and it is currently under maintenance, please check back in a few hours or checkout https://dataverse.harvard.edu/.")
  return cyp3a4_veith_model

def cyp3a4_veith(smiles):
  try:
    from DeepPurpose import utils 
  except:
    raise ImportError("Please install DeepPurpose by 'pip install DeepPurpose'")

  import os 
  os.environ["CUDA_VISIBLE_DEVICES"]='-1'  
  if 'cyp3a4_veith_model' not in globals().keys():
    global cyp3a4_veith_model 
    cyp3a4_veith_model = load_cyp3a4_veith()

  import warnings, os
  warnings.filterwarnings("ignore")

  X_drug = [smiles]
  drug_encoding = 'CNN'
  y = [1]
  X_pred = utils.data_process(X_drug = X_drug, y = y, drug_encoding = drug_encoding, split_method='no_split')
  # cyp3a4_veith_model = cyp3a4_veith_model.to("cuda:0")
  y_pred = cyp3a4_veith_model.predict(X_pred)
  return y_pred[0]

## from https://github.com/wengong-jin/iclr19-graph2graph/blob/master/props/properties.py 
## from https://github.com/wengong-jin/multiobj-rationale/blob/master/properties.py 

def similarity(a, b):
  """Evaluate Tanimoto similarity between 2 SMILES strings

    Args:
      a: str
      b: str 

    Returns:
      similarity score: float 

  """
  if a is None or b is None: 
    return 0.0
  amol = Chem.MolFromSmiles(a)
  bmol = Chem.MolFromSmiles(b)
  if amol is None or bmol is None:
    return 0.0
  fp1 = AllChem.GetMorganFingerprintAsBitVect(amol, 2, nBits=2048, useChirality=False)
  fp2 = AllChem.GetMorganFingerprintAsBitVect(bmol, 2, nBits=2048, useChirality=False)
  return DataStructs.TanimotoSimilarity(fp1, fp2) 

def qed(s):
  """Evaluate QED score of a SMILES string

    Args:
      smiles: str

    Returns:
      qed_score: float 

  """  
  if s is None: 
    return 0.0  
  mol = Chem.MolFromSmiles(s)
  if mol is None: 
    return 0.0
  return QED.qed(mol)

def penalized_logp(s):
  """Evaluate LogP score of a SMILES string

    Args:
      smiles: str

    Returns:
      logp_score: float 

  """  
  if s is None: 
    return -100.0
  mol = Chem.MolFromSmiles(s)
  if mol is None: 
    return -100.0

  logP_mean = 2.4570953396190123
  logP_std = 1.434324401111988
  SA_mean = -3.0525811293166134
  SA_std = 0.8335207024513095
  cycle_mean = -0.0485696876403053
  cycle_std = 0.2860212110245455
  log_p = Descriptors.MolLogP(mol)
  # SA = -sascorer.calculateScore(mol)
  SA = -calculateScore(mol)

  # cycle score
  cycle_list = nx.cycle_basis(nx.Graph(Chem.rdmolops.GetAdjacencyMatrix(mol)))
  if len(cycle_list) == 0:
    cycle_length = 0
  else:
    cycle_length = max([len(j) for j in cycle_list])
  if cycle_length <= 6:
    cycle_length = 0
  else:
    cycle_length = cycle_length - 6
  cycle_score = -cycle_length

  normalized_log_p = (log_p - logP_mean) / logP_std
  normalized_SA = (SA - SA_mean) / SA_std
  normalized_cycle = (cycle_score - cycle_mean) / cycle_std
  return normalized_log_p + normalized_SA + normalized_cycle


def SA(s):
  """Evaluate SA score of a SMILES string

    Args:
      smiles: str

    Returns:
      SAscore: float 

  """  
  if s is None:
    return 100 
  mol = Chem.MolFromSmiles(s)
  if mol is None:
    return 100 
  SAscore = calculateScore(mol)
  return SAscore 	

def load_gsk3b_model():
    gsk3_model_path = 'oracle/gsk3b.pkl'
    #print_sys('==== load gsk3b oracle =====')
    try:
      with open(gsk3_model_path, 'rb') as f:
          gsk3_model = pickle.load(f)
    except EOFError:
      import sys
      sys.exit("TDC is hosted in Harvard Dataverse and it is currently under maintenance, please check back in a few hours or checkout https://dataverse.harvard.edu/.")
    return gsk3_model 

def gsk3b(smiles):
    """Evaluate GSK3B score of a SMILES string

    Args:
      smiles: str

    Returns:
      gsk3_score: float 

    """  
    if 'gsk3_model' not in globals().keys():
        global gsk3_model 
        gsk3_model = load_gsk3b_model()

    molecule = smiles_to_rdkit_mol(smiles)
    fp = AllChem.GetMorganFingerprintAsBitVect(molecule, 2, nBits=2048)
    features = np.zeros((1,))
    DataStructs.ConvertToNumpyArray(fp, features)
    fp = features.reshape(1, -1) 
    gsk3_score = gsk3_model.predict_proba(fp)[0,1]
    return gsk3_score 

class jnk3:
  """Evaluate JSK3 score of a SMILES string

    Args:
      smiles: str

    Returns:
      jnk3_score: float 

  """  
  def __init__(self):
    jnk3_model_path = 'oracle/jnk3.pkl'
    try:
      with open(jnk3_model_path, 'rb') as f:
        self.jnk3_model = pickle.load(f)
    except EOFError:
      import sys
      sys.exit("TDC is hosted in Harvard Dataverse and it is currently under maintenance, please check back in a few hours or checkout https://dataverse.harvard.edu/.")
  
  def __call__(self, smiles):
    molecule = smiles_to_rdkit_mol(smiles)
    fp = AllChem.GetMorganFingerprintAsBitVect(molecule, 2, nBits=2048)
    features = np.zeros((1,))
    DataStructs.ConvertToNumpyArray(fp, features)
    fp = features.reshape(1, -1) 
    jnk3_score = self.jnk3_model.predict_proba(fp)[0,1]
    return jnk3_score

class AtomCounter:

    def __init__(self, element):
        """
        Args:
            element: element to count within a molecule
        """
        self.element = element

    def __call__(self, mol):
        """
        Count the number of atoms of a given type.

        Args:
            mol: molecule

        Returns:
            The number of atoms of the given type.
        """
        # if the molecule contains H atoms, they may be implicit, so add them
        if self.element == 'H':
            mol = Chem.AddHs(mol)

        return sum(1 for a in mol.GetAtoms() if a.GetSymbol() == self.element)

def parse_molecular_formula(formula):
    """
    Parse a molecular formulat to get the element types and counts.

    Args:
        formula: molecular formula, f.i. "C8H3F3Br"
        
    Returns:
        A list of tuples containing element types and number of occurrences.
    """
    import re 
    matches = re.findall(r'([A-Z][a-z]*)(\d*)', formula)

    # Convert matches to the required format
    results = []
    for match in matches:
        # convert count to an integer, and set it to 1 if the count is not visible in the molecular formula
        count = 1 if not match[1] else int(match[1])
        results.append((match[0], count))

    return results

class Isomer_scoring:
  def __init__(self, target_smiles, means = 'geometric'):
    assert means in ['geometric', 'arithmetic']
    if means == 'geometric':
      self.mean_func = gmean 
    else: 
      self.mean_func = np.mean 
    atom2cnt_lst = parse_molecular_formula(target_smiles)
    total_atom_num = sum([cnt for atom,cnt in atom2cnt_lst]) 
    self.total_atom_modifier = GaussianModifier(mu=total_atom_num, sigma=2.0)
    self.AtomCounter_Modifier_lst = [((AtomCounter(atom)), GaussianModifier(mu=cnt,sigma=1.0)) for atom,cnt in atom2cnt_lst]

  def __call__(self, test_smiles):
    molecule = smiles_to_rdkit_mol(test_smiles)
    all_scores = []
    for atom_counter, modifier_func in self.AtomCounter_Modifier_lst:
      all_scores.append(modifier_func(atom_counter(molecule)))

    ### total atom number
    atom2cnt_lst = parse_molecular_formula(test_smiles)
    ## todo add Hs 
    total_atom_num = sum([cnt for atom,cnt in atom2cnt_lst])
    all_scores.append(self.total_atom_modifier(total_atom_num))
    return self.mean_func(all_scores)

def isomer_meta(target_smiles, means = 'geometric'):
  return Isomer_scoring(target_smiles, means = means)

class rediscovery_meta:
  def __init__(self, target_smiles, fp = 'ECFP4'):
    self.similarity_func = fp2fpfunc[fp]
    self.target_fp = self.similarity_func(target_smiles)

  def __call__(self, test_smiles):
    test_fp = self.similarity_func(test_smiles)
    similarity_value = DataStructs.TanimotoSimilarity(self.target_fp, test_fp)
    return similarity_value 

class similarity_meta:
  def __init__(self, target_smiles, fp = 'FCFP4', modifier_func = None):
    self.similarity_func = fp2fpfunc[fp]
    self.target_fp = self.similarity_func(target_smiles)
    self.modifier_func = modifier_func 

  def __call__(self, test_smiles):
    test_fp = self.similarity_func(test_smiles)
    similarity_value = DataStructs.TanimotoSimilarity(self.target_fp, test_fp)
    if self.modifier_func is None:
      modifier_score = similarity_value
    else:
      modifier_score = self.modifier_func(similarity_value)
    return modifier_score 

class median_meta:
  def __init__(self, target_smiles_1, target_smiles_2, fp1 = 'ECFP6', fp2 = 'ECFP6', modifier_func1 = None, modifier_func2 = None, means = 'geometric'):
    self.similarity_func1 = fp2fpfunc[fp1]
    self.similarity_func2 = fp2fpfunc[fp2]
    self.target_fp1 = self.similarity_func1(target_smiles_1)
    self.target_fp2 = self.similarity_func2(target_smiles_2)
    self.modifier_func1 = modifier_func1 
    self.modifier_func2 = modifier_func2 
    assert means in ['geometric', 'arithmetic']
    self.mean_func = mean2func[means]

  def __call__(self, test_smiles):
    test_fp1 = self.similarity_func1(test_smiles)
    test_fp2 = test_fp1 if self.similarity_func2 == self.similarity_func1 else self.similarity_func2(test_smiles)
    similarity_value1 = DataStructs.TanimotoSimilarity(self.target_fp1, test_fp1)
    similarity_value2 = DataStructs.TanimotoSimilarity(self.target_fp2, test_fp2)
    if self.modifier_func1 is None:
      modifier_score1 = similarity_value1
    else:
      modifier_score1 = self.modifier_func1(similarity_value1)
    if self.modifier_func2 is None:
      modifier_score2 = similarity_value2
    else:
      modifier_score2 = self.modifier_func2(similarity_value2)
    final_score = self.mean_func([modifier_score1 , modifier_score2])
    return final_score

class MPO_meta:
  def __init__(self, means):
    '''
      target_smiles, fp in ['ECFP4', 'AP', ..., ]
      scoring, 
      modifier, 

    '''

    assert means in ['geometric', 'arithmetic']
    self.mean_func = mean2func[means]


  def __call__(self, test_smiles):
    molecule = smiles_to_rdkit_mol(test_smiles)

    score_lst = []
    return self.mean_func(score_lst)

def osimertinib_mpo(test_smiles):

  if 'osimertinib_fp_fcfc4' not in globals().keys():
    global osimertinib_fp_fcfc4, osimertinib_fp_ecfc6
    osimertinib_smiles = 'COc1cc(N(C)CCN(C)C)c(NC(=O)C=C)cc1Nc2nccc(n2)c3cn(C)c4ccccc34'
    osimertinib_fp_fcfc4 = smiles_2_fingerprint_FCFP4(osimertinib_smiles)
    osimertinib_fp_ecfc6 = smiles_2_fingerprint_ECFP6(osimertinib_smiles)


  sim_v1_modifier = ClippedScoreModifier(upper_x=0.8)
  sim_v2_modifier = MinGaussianModifier(mu=0.85, sigma=0.1)
  tpsa_modifier = MaxGaussianModifier(mu=100, sigma=10) 
  logp_modifier = MinGaussianModifier(mu=1, sigma=1) 

  molecule = smiles_to_rdkit_mol(test_smiles)
  fp_fcfc4 = smiles_2_fingerprint_FCFP4(test_smiles)
  fp_ecfc6 = smiles_2_fingerprint_ECFP6(test_smiles)
  tpsa_score = tpsa_modifier(Descriptors.TPSA(molecule))
  logp_score = logp_modifier(Descriptors.MolLogP(molecule))
  similarity_v1 = sim_v1_modifier(DataStructs.TanimotoSimilarity(osimertinib_fp_fcfc4, fp_fcfc4))
  similarity_v2 = sim_v2_modifier(DataStructs.TanimotoSimilarity(osimertinib_fp_ecfc6, fp_ecfc6))

  osimertinib_gmean = gmean([tpsa_score, logp_score, similarity_v1, similarity_v2])
  return osimertinib_gmean 

def fexofenadine_mpo(test_smiles):
  if 'fexofenadine_fp' not in globals().keys():
    global fexofenadine_fp
    fexofenadine_smiles = 'CC(C)(C(=O)O)c1ccc(cc1)C(O)CCCN2CCC(CC2)C(O)(c3ccccc3)c4ccccc4'
    fexofenadine_fp = smiles_2_fingerprint_AP(fexofenadine_smiles)

  similar_modifier = ClippedScoreModifier(upper_x=0.8)
  tpsa_modifier=MaxGaussianModifier(mu=90, sigma=10)
  logp_modifier=MinGaussianModifier(mu=4, sigma=1)

  molecule = smiles_to_rdkit_mol(test_smiles)
  fp_ap = smiles_2_fingerprint_AP(test_smiles)
  tpsa_score = tpsa_modifier(Descriptors.TPSA(molecule))
  logp_score = logp_modifier(Descriptors.MolLogP(molecule))
  similarity_value = similar_modifier(DataStructs.TanimotoSimilarity(fp_ap, fexofenadine_fp))
  fexofenadine_gmean = gmean([tpsa_score, logp_score, similarity_value])
  return fexofenadine_gmean 

def ranolazine_mpo(test_smiles):
  if 'ranolazine_fp' not in globals().keys():
    global ranolazine_fp, fluorine_counter  
    ranolazine_smiles = 'COc1ccccc1OCC(O)CN2CCN(CC(=O)Nc3c(C)cccc3C)CC2'
    ranolazine_fp = smiles_2_fingerprint_AP(ranolazine_smiles)
    fluorine_counter = AtomCounter('F')

  similar_modifier = ClippedScoreModifier(upper_x=0.7)
  tpsa_modifier = MaxGaussianModifier(mu=95, sigma=20)
  logp_modifier = MaxGaussianModifier(mu=7, sigma=1)
  fluorine_modifier = GaussianModifier(mu=1, sigma=1.0)

  molecule = smiles_to_rdkit_mol(test_smiles)
  fp_ap = smiles_2_fingerprint_AP(test_smiles)
  tpsa_score = tpsa_modifier(Descriptors.TPSA(molecule))
  logp_score = logp_modifier(Descriptors.MolLogP(molecule))
  similarity_value = similar_modifier(DataStructs.TanimotoSimilarity(fp_ap, ranolazine_fp))
  fluorine_value = fluorine_modifier(fluorine_counter(molecule))

  ranolazine_gmean = gmean([tpsa_score, logp_score, similarity_value, fluorine_value])
  return ranolazine_gmean

def perindopril_mpo(test_smiles):
  ## no similar_modifier

  if 'perindopril_fp' not in globals().keys():
    global perindopril_fp, num_aromatic_rings
    perindopril_smiles = 'O=C(OCC)C(NC(C(=O)N1C(C(=O)O)CC2CCCCC12)C)CCC'
    perindopril_fp = smiles_2_fingerprint_ECFP4(perindopril_smiles)
    def num_aromatic_rings(mol):
      return rdMolDescriptors.CalcNumAromaticRings(mol)

  arom_rings_modifier = GaussianModifier(mu = 2, sigma = 0.5)

  molecule = smiles_to_rdkit_mol(test_smiles)
  fp_ecfp4 = smiles_2_fingerprint_ECFP4(test_smiles)

  similarity_value = DataStructs.TanimotoSimilarity(fp_ecfp4, perindopril_fp)
  num_aromatic_rings_value = arom_rings_modifier(num_aromatic_rings(molecule))

  perindopril_gmean = gmean([similarity_value, num_aromatic_rings_value])
  return perindopril_gmean

def amlodipine_mpo(test_smiles):
  ## no similar_modifier
  if 'amlodipine_fp' not in globals().keys():
    global amlodipine_fp, num_rings
    amlodipine_smiles = 'Clc1ccccc1C2C(=C(/N/C(=C2/C(=O)OCC)COCCN)C)\C(=O)OC'
    amlodipine_fp = smiles_2_fingerprint_ECFP4(amlodipine_smiles)
  
    def num_rings(mol):
      return rdMolDescriptors.CalcNumRings(mol)  
  num_rings_modifier = GaussianModifier(mu=3, sigma=0.5)

  molecule = smiles_to_rdkit_mol(test_smiles)
  fp_ecfp4 = smiles_2_fingerprint_ECFP4(test_smiles)

  similarity_value = DataStructs.TanimotoSimilarity(fp_ecfp4, amlodipine_fp)
  num_rings_value = num_rings_modifier(num_rings(molecule))

  amlodipine_gmean = gmean([similarity_value, num_rings_value])
  return amlodipine_gmean

def zaleplon_mpo(test_smiles):
  if 'zaleplon_fp' not in globals().keys():
    global zaleplon_fp, isomer_scoring_C19H17N3O2
    zaleplon_smiles = 'O=C(C)N(CC)C1=CC=CC(C2=CC=NC3=C(C=NN23)C#N)=C1'
    zaleplon_fp = smiles_2_fingerprint_ECFP4(zaleplon_smiles)
    isomer_scoring_C19H17N3O2 = Isomer_scoring(target_smiles = 'C19H17N3O2')

  fp = smiles_2_fingerprint_ECFP4(test_smiles)
  similarity_value = DataStructs.TanimotoSimilarity(fp, zaleplon_fp)
  isomer_value = isomer_scoring_C19H17N3O2(test_smiles)
  return gmean([similarity_value, isomer_value])

def sitagliptin_mpo(test_smiles):
  if 'sitagliptin_fp_ecfp4' not in globals().keys():
    global sitagliptin_fp_ecfp4, sitagliptin_logp_modifier, sitagliptin_tpsa_modifier, \
           isomers_scoring_C16H15F6N5O, sitagliptin_similar_modifier
    sitagliptin_smiles = 'Fc1cc(c(F)cc1F)CC(N)CC(=O)N3Cc2nnc(n2CC3)C(F)(F)F'
    sitagliptin_fp_ecfp4 = smiles_2_fingerprint_ECFP4(sitagliptin_smiles)
    sitagliptin_mol = Chem.MolFromSmiles(sitagliptin_smiles)
    sitagliptin_logp = Descriptors.MolLogP(sitagliptin_mol)
    sitagliptin_tpsa = Descriptors.TPSA(sitagliptin_mol)
    sitagliptin_logp_modifier = GaussianModifier(mu=sitagliptin_logp, sigma=0.2)
    sitagliptin_tpsa_modifier = GaussianModifier(mu=sitagliptin_tpsa, sigma=5)
    isomers_scoring_C16H15F6N5O = Isomer_scoring('C16H15F6N5O')
    sitagliptin_similar_modifier = GaussianModifier(mu=0, sigma=0.1)

  molecule = Chem.MolFromSmiles(test_smiles)
  fp_ecfp4 = smiles_2_fingerprint_ECFP4(test_smiles)
  logp_score = Descriptors.MolLogP(molecule)
  tpsa_score = Descriptors.TPSA(molecule)
  isomer_score = isomers_scoring_C16H15F6N5O(test_smiles)
  similarity_value = DataStructs.TanimotoSimilarity(fp_ecfp4, sitagliptin_fp_ecfp4)
  return gmean([similarity_value, logp_score, tpsa_score, isomer_score])

def get_PHCO_fingerprint(mol):
  if 'Gobbi_Pharm2D' not in globals().keys():
    global Gobbi_Pharm2D, Generate
    from rdkit.Chem.Pharm2D import Generate, Gobbi_Pharm2D
  return Generate.Gen2DFingerprint(mol, Gobbi_Pharm2D.factory)

class SMARTS_scoring:
  def __init__(self, target_smarts, inverse):
    self.target_mol = Chem.MolFromSmarts(target_smarts)
    self.inverse = inverse

  def __call__(self, mol):
    matches = mol.GetSubstructMatches(self.target_mol)
    if len(matches) > 0:
      if self.inverse:
        return 0.0
      else:
        return 1.0
    else:
      if self.inverse:
        return 1.0
      else:
        return 0.0

def deco_hop(test_smiles):
  if 'pharmacophor_fp' not in globals().keys():
    global pharmacophor_fp, deco1_smarts_scoring, deco2_smarts_scoring, scaffold_smarts_scoring   
    pharmacophor_smiles = 'CCCOc1cc2ncnc(Nc3ccc4ncsc4c3)c2cc1S(=O)(=O)C(C)(C)C'
    pharmacophor_mol = smiles_to_rdkit_mol(pharmacophor_smiles)
    pharmacophor_fp = get_PHCO_fingerprint(pharmacophor_mol)

    deco1_smarts_scoring = SMARTS_scoring(target_smarts = 'CS([#6])(=O)=O', inverse = True)
    deco2_smarts_scoring = SMARTS_scoring(target_smarts = '[#7]-c1ccc2ncsc2c1', inverse = True) 
    scaffold_smarts_scoring = SMARTS_scoring(target_smarts = '[#7]-c1n[c;h1]nc2[c;h1]c(-[#8])[c;h0][c;h1]c12', inverse = False) 

  molecule = smiles_to_rdkit_mol(test_smiles)
  fp = get_PHCO_fingerprint(molecule)
  similarity_modifier = ClippedScoreModifier(upper_x=0.85)

  similarity_value = similarity_modifier(DataStructs.TanimotoSimilarity(fp, pharmacophor_fp))
  deco1_score = deco1_smarts_scoring(molecule)
  deco2_score = deco2_smarts_scoring(molecule)
  scaffold_score = scaffold_smarts_scoring(molecule)

  all_scores = np.mean([similarity_value, deco1_score, deco2_score, scaffold_score])
  return all_scores

def scaffold_hop(test_smiles):
  if 'pharmacophor_fp' not in globals().keys() \
      or 'scaffold_smarts_scoring' not in globals().keys() \
      or 'deco_smarts_scoring' not in globals().keys():
    global pharmacophor_fp, deco_smarts_scoring, scaffold_smarts_scoring   
    pharmacophor_smiles = 'CCCOc1cc2ncnc(Nc3ccc4ncsc4c3)c2cc1S(=O)(=O)C(C)(C)C'
    pharmacophor_mol = smiles_to_rdkit_mol(pharmacophor_smiles)
    pharmacophor_fp = get_PHCO_fingerprint(pharmacophor_mol)

    deco_smarts_scoring = SMARTS_scoring(target_smarts = '[#6]-[#6]-[#6]-[#8]-[#6]~[#6]~[#6]~[#6]~[#6]-[#7]-c1ccc2ncsc2c1', 
                                         inverse=False)

    scaffold_smarts_scoring = SMARTS_scoring(target_smarts = '[#7]-c1n[c;h1]nc2[c;h1]c(-[#8])[c;h0][c;h1]c12', 
                                             inverse=True)

  molecule = smiles_to_rdkit_mol(test_smiles)
  fp = get_PHCO_fingerprint(molecule)
  similarity_modifier = ClippedScoreModifier(upper_x=0.75)

  similarity_value = similarity_modifier(DataStructs.TanimotoSimilarity(fp, pharmacophor_fp))
  deco_score = deco_smarts_scoring(molecule)
  scaffold_score = scaffold_smarts_scoring(molecule)

  all_scores = np.mean([similarity_value, deco_score, scaffold_score])
  return all_scores

def valsartan_smarts(test_smiles):
  if 'valsartan_logp_modifier' not in globals().keys():
    global valsartan_mol, valsartan_logp_modifier, valsartan_tpsa_modifier, valsartan_bertz_modifier
    valsartan_smarts = 'CN(C=O)Cc1ccc(c2ccccc2)cc1' ### smarts 
    valsartan_mol = Chem.MolFromSmarts(valsartan_smarts)

    sitagliptin_smiles = 'NC(CC(=O)N1CCn2c(nnc2C(F)(F)F)C1)Cc1cc(F)c(F)cc1F' ### other mol
    sitagliptin_mol = Chem.MolFromSmiles(sitagliptin_smiles)

    target_logp = Descriptors.MolLogP(sitagliptin_mol)
    target_tpsa = Descriptors.TPSA(sitagliptin_mol)
    target_bertz = Descriptors.BertzCT(sitagliptin_mol)

    valsartan_logp_modifier = GaussianModifier(mu=target_logp, sigma=0.2)
    valsartan_tpsa_modifier = GaussianModifier(mu=target_tpsa, sigma=5)
    valsartan_bertz_modifier = GaussianModifier(mu=target_bertz, sigma=30)

  molecule = smiles_to_rdkit_mol(test_smiles)
  matches = molecule.GetSubstructMatches(valsartan_mol)
  if len(matches) > 0:
    smarts_score = 1.0
  else:
    smarts_score = 0.0

  logp_score = valsartan_logp_modifier(Descriptors.MolLogP(molecule))
  tpsa_score = valsartan_tpsa_modifier(Descriptors.TPSA(molecule))
  bertz_score = valsartan_bertz_modifier(Descriptors.BertzCT(molecule))
  valsartan_gmean = gmean([smarts_score, tpsa_score, logp_score, bertz_score])
  return valsartan_gmean

###########################################################################
###               END of Guacamol
###########################################################################


'''
Synthesizability from a full retrosynthetic analysis
Including:
    1. MIT ASKCOS
    ASKCOS (https://askcos.mit.edu) is an open-source software 
    framework that integrates efforts to generalize known chemistry 
    to new substrates by learning to apply retrosynthetic transformations, 
    to identify suitable reaction conditions, and to evaluate whether 
    reactions are likely to be successful. The data-driven models are trained 
    with USPTO and Reaxys databases.
    
    Reference:
    https://doi.org/10.1021/acs.jcim.0c00174

    2. IBM_RXN
    IBM RXN (https://rxn.res.ibm.com) is an AI platform integarting 
    forward reaction prediction and retrosynthetic analysis. The 
    backend of the IBM RXN retrosynthetic analysis is Molecular 
    Transformer model (see reference). The model was mainly trained 
    with USPTO, Pistachio databases.
    Reference:
    https://doi.org/10.1021/acscentsci.9b00576
'''

def tree_analysis(current):
    """
    Analyze the result of tree builder
    Calculate: 1. Number of steps 2. \Pi plausibility 3. If find a path
    In case of celery error, all values are -1
    
    return:
        num_path = number of paths found
        status: Same as implemented in ASKCOS one
        num_step: number of steps
        p_score: \Pi plausibility
        synthesizability: binary code
        price: price for synthesize query compound
    """
    if 'error' in current.keys():
        return -1, {}, 11, -1, -1, -1
    
    if 'price' in current.keys():
        return 0, {}, 0, 1, 1, current['price']
    
    num_path = len(current['trees'])
    if num_path != 0:
        current = [current['trees'][0]]
        if current[0]['ppg'] != 0:
            return 0, {}, 0, 1, 1, current[0]['ppg']
    else:
        current = []
        
    depth = 0
    p_score = 1
    status = {0:1}
    price = 0
    while True:
        num_child = 0
        depth += 0.5
        temp = []
        for i, item in enumerate(current):
            num_child += len(item['children'])
            temp = temp + item['children']
        if num_child == 0:
            break
        if depth % 1 != 0:
            for sth in temp:
                p_score = p_score * sth['plausibility']
        else:
            for mol in temp:
                price += mol['ppg']
        status[depth] = num_child
        current = temp
    if len(status) > 1:
        synthesizability = 1
    else:
        synthesizability = 0
    if int(depth - 0.5) == 0:
        depth = 11
        price = -1
    else:
        depth = int(depth - 0.5)
    return num_path, status, depth, p_score*synthesizability, synthesizability, price


def askcos(smiles, host_ip, output='plausibility', save_json=False, file_name='tree_builder_result.json', num_trials=5,
           max_depth=9, max_branching=25, expansion_time=60, max_ppg=100, template_count=1000, max_cum_prob=0.999, 
           chemical_property_logic='none', max_chemprop_c=0, max_chemprop_n=0, max_chemprop_o=0, max_chemprop_h=0, 
           chemical_popularity_logic='none', min_chempop_reactants=5, min_chempop_products=5, filter_threshold=0.1, return_first='true'):
    """
    The ASKCOS retrosynthetic analysis oracle function. 
    Please refer https://github.com/connorcoley/ASKCOS to run the ASKCOS with docker on a server to receive requests.
    """

    if output not in ['num_step', 'plausibility', 'synthesizability', 'price']:
        raise NameError("This output value is not implemented. Please select one from 'num_step', 'plausibility', 'synthesizability', 'price'.")
    
    import json, requests
    
    params = {
        'smiles': smiles
    }
    resp = requests.get(host_ip+'/api/price/', params=params, verify=False)

    if resp.json()['price'] == 0:
        # Parameters for Tree Builder
        params = {
            'smiles': smiles, 

            # optional
            'max_depth': max_depth,
            'max_branching': max_branching,
            'expansion_time': expansion_time,
            'max_ppg': max_ppg,
            'template_count': template_count,
            'max_cum_prob': max_cum_prob,
            'chemical_property_logic': chemical_property_logic,
            'max_chemprop_c': max_chemprop_c,
            'max_chemprop_n': max_chemprop_n,
            'max_chemprop_o': max_chemprop_o,
            'max_chemprop_h': max_chemprop_h,
            'chemical_popularity_logic': chemical_popularity_logic,
            'min_chempop_reactants': min_chempop_reactants,
            'min_chempop_products': min_chempop_products,
            'filter_threshold': filter_threshold,
            'return_first': return_first
        }

        # For each entry, repeat to test up to num_trials times if got error message
        for _ in range(num_trials):
            print('Trying to send the request, for the %i times now' % (_ + 1))
            resp = requests.get(host_ip + '/api/treebuilder/', params=params, verify=False)
            if 'error' not in resp.json().keys():
                break
                
    if save_json:
        with open(file_name, 'w') as f_data:
            json.dump(resp.json(), f_data)
        
    num_path, status, depth, p_score, synthesizability, price = tree_analysis(resp.json())
    
    if output == 'plausibility':
        return p_score
    elif output == 'num_step':
        return depth
    elif output == 'synthesizability':
        return synthesizability
    elif output == 'price':
        return price

def ibm_rxn(smiles, api_key, output='confidence', sleep_time=30):
    """
    This function is modified from Dr. Jan Jensen's code
    """
    try:
      from rxn4chemistry import RXN4ChemistryWrapper
    except:
      print_sys("Please install rxn4chemistry via pip install rxn4chemistry")
    import time
    
    rxn4chemistry_wrapper = RXN4ChemistryWrapper(api_key=api_key)
    response = rxn4chemistry_wrapper.create_project('test')
    time.sleep(sleep_time)
    response = rxn4chemistry_wrapper.predict_automatic_retrosynthesis(product=smiles)
    status = ''
    while status != 'SUCCESS':
        time.sleep(sleep_time)
        results = rxn4chemistry_wrapper.get_predict_automatic_retrosynthesis_results(response['prediction_id'])
        status = results['status']

    if output == 'confidence':
        return results['retrosynthetic_paths'][0]['confidence']
    elif output == 'result':
        return results
    else:
        raise NameError("This output value is not implemented.")

class molecule_one_retro:

    def __init__(self, api_token):
      try:
          from m1wrapper import MoleculeOneWrapper
      except:
          try:
              install('git+https://github.com/molecule-one/m1wrapper-python')
          except:
              raise ImportError("Install Molecule.One Wrapper via pip install git+https://github.com/molecule-one/m1wrapper-python") 
              from m1wrapper import MoleculeOneWrapper
      self.m1wrapper = MoleculeOneWrapper(api_token, 'https://tdc.molecule.one')
    

    def __call__(self, smiles):
      if isinstance(smiles, str):
          smiles = [smiles]

      search = self.m1wrapper.run_batch_search(
          targets=smiles,
          parameters={'exploratory_search': False, 'detail_level': 'score'}
      )

      status_cur = search.get_status()
      print_sys('Started Querying...')
      print_sys(status_cur)
      while True:
          time.sleep(7)
          status = search.get_status()

          if (status['queued'] == 0) and (status['running'] == 0):
              print_sys('Finished... Returning Results...')
              break
          else:
              if status_cur != status:
                  print_sys(status)
          status_cur = status
      result = search.get_results(precision=5, only=["targetSmiles", "result"])
      return {i['targetSmiles']: i['result'] for i in result}

class docking_meta:
    def __init__(self, software_calss='vina', pyscreener_path = './pyscreener', **kwargs):
        import sys
        sys.path.append(pyscreener_path)
        if software_calss == 'vina':
            from pyscreener.docking.vina import Vina as screener
        elif software_calss == 'dock6':
            from pyscreener.docking.dock import DOCK as screener
        else:
            raise ValueError("The value of software_calss is not implemented. Currently available:['vina', 'dock6']")

        self.scorer = screener(**kwargs)

    def __call__(self, test_smiles):
        final_score = self.scorer(test_smiles)
        if type(test_smiles)==str:
          return list(final_score.values())[0]
        else:  ## list 
          # dict: {'O=C(/C=C/c1ccc([N+](=O)[O-])o1)c1ccc(-c2ccccc2)cc1': -9.9, 'CCOc1cc(/C=C/C(=O)C(=Cc2ccc(O)c(OC)c2)C(=O)/C=C/c2ccc(O)c(OCC)c2)ccc1O': -9.1}
          # return [list(i.values())[0] for i in final_score]
          score_lst = []
          for smiles in test_smiles:
            score = final_score[smiles]
            if score is None:
              score = 0.0 
            score_lst.append(score)
          return score_lst