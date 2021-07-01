def _parse_prop(search, proplist):
	"""Extract property value from record using the given urn search filter."""
	props = [i for i in proplist if all(item in i['urn'].items() for item in search.items())]
	if len(props) > 0:
		return props[0]['value'][list(props[0]['value'].keys())[0]]

def request(identifier, namespace='cid', domain='compound', operation=None, output='JSON', searchtype=None):
	"""
	copied from https://github.com/mcs07/PubChemPy/blob/e3c4f4a9b6120433e5cc3383464c7a79e9b2b86e/pubchempy.py#L238
	Construct API request from parameters and return the response.
	Full specification at http://pubchem.ncbi.nlm.nih.gov/pug_rest/PUG_REST.html
	"""
	API_BASE = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug'
	text_types = str, bytes
	if not identifier:
		raise ValueError('identifier/cid cannot be None')
	# If identifier is a list, join with commas into string
	if isinstance(identifier, int):
		identifier = str(identifier)
	if not isinstance(identifier, text_types):
		identifier = ','.join(str(x) for x in identifier)

	# Build API URL
	urlid, postdata = None, None
	if namespace == 'sourceid':
		identifier = identifier.replace('/', '.')
	if namespace in ['listkey', 'formula', 'sourceid'] \
			or searchtype == 'xref' \
			or (searchtype and namespace == 'cid') or domain == 'sources':
		urlid = quote(identifier.encode('utf8'))
	else:
		postdata = urlencode([(namespace, identifier)]).encode('utf8')
	comps = filter(None, [API_BASE, domain, searchtype, namespace, urlid, operation, output])
	apiurl = '/'.join(comps)
	# Make request
	response = urlopen(apiurl, postdata)
	return response

def uniprot2seq(ProteinID):
	"""Get protein sequence from Uniprot ID

	Parameters
	----------
	ProteinID : str
		Uniprot ID

	Returns
	-------
	str
		Amino acid sequence of input uniprot ID
	"""
	import urllib
	import string
	import urllib.request as ur

	ID = str(ProteinID)
	localfile = ur.urlopen('http://www.uniprot.org/uniprot/' + ID + '.fasta')
	temp = localfile.readlines()
	res = ''
	for i in range(1, len(temp)):
		res = res + temp[i].strip().decode("utf-8")
	return res

def cid2smiles(cid):
	try:
		smiles = _parse_prop({'label': 'SMILES', 'name': 'Canonical'}, json.loads(request(cid).read().decode())['PC_Compounds'][0]['props'])
	except:
		print('cid ' + str(cid) + ' failed, use NULL string')
		smiles = 'NULL'
	return smiles