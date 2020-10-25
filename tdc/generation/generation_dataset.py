import pandas as pd
import numpy as np
import os, sys, json 
import warnings
warnings.filterwarnings("ignore")

from .. import base_dataset
from ..utils import *


class DataLoader(base_dataset.DataLoader):
	pass 

class PairedDataLoader(base_dataset.DataLoader):
	def __init__(self, name, path, print_stats, input_name, output_name):
		'''
		Arguments:
			name: fuzzy name of the generation dataset. e.g., uspto50k, qed, drd, ... 
			path: directory path that stores the dataset, e.g., ./data
			print_stats: bool, whether print the stats.  
		returns:
			None
		'''
		from ..utils import paired_dataset_names 
		self.input_smiles_lst, self.output_smiles_lst = generation_paired_dataset_load(name, path, 
																					   paired_dataset_names, input_name, 
																					   output_name)  ### including fuzzy-search 
		self.name = name 
		self.path = path 
		self.dataset_names = paired_dataset_names
		if print_stats: 
			self.print_stats() 

	# def __init__(self, name, path, print_stats, dataset_names):
	# 	if name.lower() in retrosyn_dataset_names.keys():  
	# 		print_sys("Tip: Use tdc.utils.target_list('" + name.lower() + "') to retrieve all available label targets.")

	def print_stats(self):
		print("There are " + str(len(self.input_smiles_lst)) + ' paired samples', flush = True, file = sys.stderr)


	def get_data(self, format = 'df'):
		if format == 'df':
			return pd.DataFrame({'input': self.input_smiles_lst, 'output':self.output_smiles_lst})
		elif format == 'dict':
			return {'input': self.input_smiles_lst, 'output':self.output_smiles_lst} 
		else:
			raise AttributeError("Please use the correct format input")


	def get_split(self, method = 'random', seed = 'benchmark', frac = [0.7, 0.1, 0.2]):
		'''
		Arguments:
			method: splitting schemes: random, cold_drug, cold_target
			seed: 'benchmark' seed set to 1234, or int values
			frac: train/val/test split
		'''
		if seed == 'benchmark':
			seed = 1234

		df = self.get_data(format = 'df')

		if method == 'random':
			return create_fold(df, seed, frac)
		else:
			raise AttributeError("Please use the correct split method")



class Evaluator:

	def __init__(self, name):
		from ..utils import property_names ### e.g., ['drd2', 'qed', 'logp']
		self.name = fuzzy_search(name, property_names)
		#### plogp, drd, .... 
		self.evaluator_func = lambda x:1 

	def __call__(self, smiles):
		if type(smiles)==list:
			return list(map(self.evaluator_func, smiles))
		else: ### type(smiles)==str:
			return self.evaluator_func(smiles)



## test for Evaluator

# if __name__ == "__main__":
# 	logp_evaluate = Evaluator(name = "plogp")
# 	print(logp_evaluate(''))
# 	print(logp_evaluate([1,2,3]))


if __name__ == "__main__":
	paired_dataloader = PairedDataLoader(name = 'uspto50k', path = './data', 
										 print_stats = True, input_name = 'reactants', 
										 output_name = 'product')

	input_data, output_data = paired_dataloader.get_data()





