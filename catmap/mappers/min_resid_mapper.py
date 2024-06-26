from .mapper_base import *

class MinResidMapper(MapperBase):
    """Mapper which uses initial guesses with minimum residual."""
    def __init__(self,reaction_model = None):
        MapperBase.__init__(self,reaction_model)
        defaults = dict(
                search_directions = [[0,0],[0,1],[1,0],[0,-1],
                    [-1,0],[-1,1],[1,1],[1,-1],[-1,-1]],
                max_bisections = 3,
                max_initial_guesses = 3,
                descriptor_decimal_precision = 2,
                extrapolate_coverages = False,
                force_recompilation = False,
                )
        for v in self.output_variables:
            defaults['_'+v+'_map'] = None

        self._rxm.update(defaults,override=False)
        self._required = {'search_directions':list,
                'max_bisections':int,
                'descriptor_decimal_precision':int,
                'extrapolate_coverages':bool}
        self._log_strings = {
                'bisection_success':
                "moved from ${old_pt} to ${new_pt}",
                'bisection_fail':
                "move from ${old_pt} to ${new_pt}",
                'single_point_fail':
                "failed to find solution with initial guess ${i} at ${new_pt}; trying next guess.",
                'single_point_success':
                "successfully found solution with initial guess ${i} at ${new_pt}",
                'bisection_maxiter':
                "maximum iterations bisecting from ${old_pt} to ${new_pt}",
                'minresid_success':
                "${pt} using coverages from ${old_pt}",
                'minresid_status':
                "trying coverages from ${old_pt} at ${pt}",
                'minresid_fail':
                "${pt} using coverages from ${old_pt}; initial residual"+\
                        " was ${old_resid} (residual = ${resid})",
                'initial_success':
                "initial guess at point ${pt}",
                'initial_fail':
                "initial guess at point ${pt}",
                'initial_invalid':
                "initial guess at point ${pt}",
                'mapper_status':
                "${n_unmapped} points do not have valid solution.",
                'mapper_success':
                "found solutions at all points.",
                'mapper_fail':
                "no solution at ${n_unmapped} points.",
                'rate_success':
                "found rates at ${pt}",
                'rate_fail':
                "no coverages at ${pt}"
                        }

    def get_initial_coverage(self, descriptors, *args, **kwargs):
        """
            Return initial guess for coverages based on Boltzmann weights.
            The return format is [descriptors, [coverages]] where the list
            of coverages represents the initial guess for different choices
            for gas phase-reservoirs that are in equilibrium with the
            surface coverages.

            :param descriptors: [float]
            :type descriptors: A list of descriptor values, like [.5, .5]
            :param *args: see catmap.solver.get_initial_coverages
            :type *args: []
            :param **kwargs: see catmap.solver.get_initial_coverages
            :type **kwargs: {}

        """
        params = self.scaler.get_rxn_parameters(descriptors)
        solver_cvgs = self.solver.get_initial_coverage(params,*args,**kwargs)
        return solver_cvgs

    def get_initial_coverage_from_map(self, descriptors, *args,**kwargs):
        """

        """
        resid_cvg = []
        for pt, cvgs in self.coverage_map:
            resid = self.solver.get_residual(cvgs)
            resid_cvg.append([resid,cvgs])
        resid_cvg.sort()
        if self.max_initial_guesses:
            max_len = max(self.max_initial_guesses,len(resid_cvg))
        else:
            max_len = len(resid_cvg)
        resids,cvgs = zip(*resid_cvg)
        return cvgs[:max_len]

    def get_point_coverage(self, descriptors, *args, **kwargs):
        """Shortcut to get final coverages at a point.

            :param descriptors: List of chemical descriptors, like [-.5, -.5]
            :type descriptors: [float]
            :param *args: see catmap.solvers.get_coverage
            :type *args: []
            :param **kwargs: see catmap.solver.get_coverage


        """
        #Check to see if point has already been solved
        if self.use_numbers_solver:
            # If it has already been solved, it must have 
            # a numbers_map
            current = self.retrieve_data(self._numbers_map,
                                         descriptors,
                                         self.descriptor_decimal_precision)
            # set as the _numbers attribute
            self._numbers = current
        else:
            current= self.retrieve_data(self._coverage_map,
                    descriptors,
                    self.descriptor_decimal_precision)
        if current:
            return current
        if self.force_recompilation:
            self._compiled = False
            self.solver.compile()
        self._descriptors = descriptors
        params = self.scaler.get_rxn_parameters(descriptors)
        self._rxn_parameters = params
        if not args and 'c0' not in kwargs:
            initial_guesses = self.get_initial_coverage_from_map(descriptors)
            for i,ci in enumerate(initial_guesses):
                kwargs['c0'] = ci
                cvgs = None
                try:
                    cvgs = self.solver.get_coverage(params,*args,**kwargs)
                    self.log('single_point_success',new_pt=descriptors,i=i)
                    break
                except ValueError as strerror:
                    self.log('single_point_fail',new_pt=descriptors,i=i)
        else:
            cvgs = self.solver.get_coverage(params,*args,**kwargs)
        self._coverage = cvgs
        return cvgs

    def bisect_descriptor_line(self, new_descriptors, old_descriptors,
            initial_guess_quantity):
        """Find a quantity, either coverages or x at point new_descriptors
        given that coverages or x are initial_guess_quantity at old_descriptors 
        by incrementally halving the distance between points upon failure 
        to converge.

            :param new_descriptors: list of descriptors that fails
            :type new_descriptors: [float]
            :param old_descriptors: list of descriptors that is known to work
            :type old_descriptors: [float]
            :param inititial_guess_quantity: List of best of guess for coverages
            :type initial_guess_quantity: [float]

        """

        def extrapolate_coverages(
                descriptors0, quantities0, descriptors1, quantities1, descriptors2):
            """Helper function to linearly extrapolate guess from two points."""
            dx = mp.matrix(quantities1) - mp.matrix(quantities0)
            dY = mp.matrix(descriptors1) - mp.matrix(descriptors0)
            dy = mp.sqrt(mp.fsum([mp.power(y,2) for y in dY]))
            dY2 = mp.matrix(descriptors2) - mp.matrix(descriptors0)
            dy2 = mp.sqrt(mp.fsum([mp.power(y,2) for y in dY2]))
            m = mp.matrix([mp.fdiv(dxi,dy) for dxi in dx])
            coverages2 = mp.matrix(quantities0) + m*dy2
            return coverages2

        def get_next_valid_coverages(new_descriptors, old_descriptors, old_quantity):
            """
            Helper function to get the closest point where the old guesses converge.
            """

            # Keep track of quantities
            current_descriptors = new_descriptors
            quantity = old_quantity

            if self.use_numbers_solver:
                coverages = self.solver.change_x_to_theta(quantity)
            else:
                coverages = quantity

            # Keep track of iterations
            VCiter = 0
            VCconverged = False

            while VCconverged == False and VCiter < self.max_bisections:
                VCiter += 1

                try:
                    self.get_point_output(current_descriptors, quantity)
                    coverages = self._coverage

                    if self.use_numbers_solver:
                        numbers = self._numbers

                    # If we have gotten so far then we have a valid point
                    VCconverged = True

                    self.log('bisection_success',
                            old_pt = self.print_point(old_descriptors),
                            new_pt = self.print_point(current_descriptors),
                            n_iter = VCiter)

                    if self.use_numbers_solver:
                        return current_descriptors, numbers
                    else:
                        return current_descriptors, coverages

                except ValueError:
                    # The bisection did not work
                    resid = float(self.solver.get_residual(coverages))
                    self.log('bisection_fail',
                            old_pt = self.print_point(old_descriptors),
                            new_pt = self.print_point(current_descriptors),
                            n_iter = VCiter,
                            resid = resid)

                if VCconverged == False:
                    # Bisect again
                    current_descriptors = [(float(d1)+float(d2))/float(2.0)
                            for d1,d2
                            in zip(old_descriptors,current_descriptors)]

            if VCconverged == False:
                raise ValueError('Could not find a valid solution at ' + \
                        self.print_point(current_descriptors) + \
                        ' (Extrapolated from ' + \
                        self.print_point(old_descriptors) + \
                        ' after '+str(self.max_bisections)+' bisections.' + \
                        ' (resid=' + str(float(resid)) +'))')

            if self.use_numbers_solver:
                return current_descriptors, numbers
            else:
                return current_descriptors, coverages

        # Iterate to next point via bisections
        PCiter =0
        solved_descriptors = old_descriptors
        current_descriptors = new_descriptors
        current_quantity = initial_guess_quantity

        # Store the coverages
        coverage_map = []

        # Carry out the bisection
        # quantity can refer to either coverages or x
        PCconverged = False
        descriptors0 = None 
        descriptors1 = None
        quantity0 = None
        quantity1 = None

        # Begin the bisection loop
        while PCconverged == False and PCiter <= 2**self.max_bisections:
            PCiter+=1
            descriptors0 = solved_descriptors
            quantity0 = current_quantity

            # Current quantity is a coverage or x value depending on solver
            solved_descriptors, current_quantity = get_next_valid_coverages(
                                                    current_descriptors,
                                                    solved_descriptors,
                                                    current_quantity)

            descriptors1 = solved_descriptors
            quantity1 = current_quantity

            if self.use_numbers_solver:
                current_coverage = self.solver.change_x_to_theta(current_quantity)
                # Cutoff the empty sites to store only the 
                # coverage of species in the coverage_map, the 
                # subtraction by 1 is to remove 'g', which is 
                # not included in the solver
                # esites = len(self.site_names) - 1
                self._coverage_map.append([ solved_descriptors, current_coverage])
                self._numbers_map.append([ solved_descriptors, self._numbers])
            else:
                self._coverage_map.append([solved_descriptors,current_quantity])

            if solved_descriptors == new_descriptors:
                PCconverged = True
                return current_quantity

            elif self.extrapolate_coverages == True:
                current_quantity = extrapolate_coverages(
                        descriptors0,
                        quantity0,
                        descriptors1,
                        quantity1,
                        current_descriptors)
                #extrapolate coverages as initial guess

        if PCconverged == True:
            return current_quantity
        else:
            raise ValueError(str(self.max_bisections) + ' bisections were not '+\
                    'sufficient to move from ' + \
                    self.print_point(old_descriptors) + ' to ' + \
                    self.print_point(new_descriptors))

    def get_coverage_map(self, descriptor_ranges=None, resolution = None,
            initial_guess_adsorbate_names=None):
        """
        Creates coverage map by computing residuals from nearby points
        and trying points with lowest residuals first
        """
        d1Vals, d2Vals = self.process_resolution(descriptor_ranges, resolution)
        d1Vals = d1Vals[::-1]
        d2Vals = d2Vals[::-1]

        #matrix to track which
        #values have been checked/which directions have been searched
        isMapped = np.zeros((len(d1Vals),len(d2Vals)), dtype=int) 
        maxNum = int('1'*len(self.search_directions), 2) #if number is higher
        #than this then the point should not be checked
        #(use binary representation to determine which directions have
        #been checked). Number can't be higher than having a
        #1 in every direction.

        if self.use_numbers_solver and self.coverage_map is not None:
            # It is a numbers solver run and if the coverage map and numbers
            # map has been stored from the previous run then use those
            if self.numbers_map is None:
                # The numbers solver does not currently have implemented
                # support for reading in solutions from previous runs.
                raise NotImplementedError('Delete .pickle and .log file for using numbers solver')
            # We should have all the initial guesses needed to 
            # perform the CatMAP calculation now 
            self._numbers_map = []
            self._coverage_map = []
            initial_guess_coverage_map = [c for c in self.coverage_map]
            initial_guess_numbers_map = [n for n in self.numbers_map]

        elif self.use_numbers_solver and self.coverage_map is None:
            # The numbers solver is being used but it is not a repeat run
            initial_guess_coverage_map = None
            self._numbers_map = []
            self._coverage_map = []

        elif not self.use_numbers_solver and self.coverage_map is not None:
            # Not using the numbers solver and it is a repeat run
            initial_guess_coverage_map = [c for c in self.coverage_map]
            self._coverage_map = []
        
        elif not self.use_numbers_solver and self.coverage_map is None:
            # Neither a numbers solver nor a repeat run
            initial_guess_coverage_map = None
            self._coverage_map = []
        else:
            raise ValueError('Invalid combination of options, \
                delete .pickle and .log file and try again')
        
        #Clause for obtaining initial coverages from an initial guess map
        if initial_guess_coverage_map:

            initial_guess_coverage_map = sorted(initial_guess_coverage_map)
            initial_guess_coverage_map.reverse()

            if self.use_numbers_solver:
                initial_guess_quantities = initial_guess_numbers_map
            else:
                initial_guess_quantities = initial_guess_coverage_map

            for point,guess_coverage in initial_guess_quantities:
                #Re-organize the values of guess coverages to match the
                #adsorbates for this system (if they came from the results
                #of another simulation)
                if initial_guess_adsorbate_names:
                    new_guess_coverage = mp.matrix(1,len(self.adsorbate_names))
                    for old_ads in initial_guess_adsorbate_names:
                        if old_ads in self.adsorbate_names:
                            idx = self.adsorbate_names.index(old_ads)
                            old_idx = initial_guess_adsorbate_names.index(
                                    old_ads)
                            new_guess_coverage[idx] = guess_coverage[old_idx]
                    guess_coverage = new_guess_coverage
                elif len(guess_coverage) < len(self.adsorbate_names):
                    if self.verbose:
                        print('Length of guess coverage vectors are shorter' + \
                                ' than the number of adsorbates. Assuming' + \
                                ' undefined coverages are 0')
                    new_guess_coverage = mp.matrix(1,len(self.adsorbate_names))
                    for n,cvg in enumerate(guess_coverage):
                        new_guess_coverage[n] = cvg
                    guess_coverage = new_guess_coverage
                elif len(guess_coverage) > len(self.adsorbate_names):
                    right_size = len(self.adsorbate_names) + len(self.site_names) - 1
                    if self.use_numbers_solver and len(guess_coverage) == right_size:
                        # No issue here as the slab x value is also saved.
                        cut_coverage = False
                    else:
                        cut_coverage = True
                    if cut_coverage:
                        if self.verbose:
                            print('Length of guess coverage vector is longer than \
                                    the number of adsorbates.  \
                                    Discarding extra coverages.')
                        new_guess_coverage = mp.matrix(1,right_size)
                        for n,cvg in enumerate(guess_coverage[0:len(
                            new_guess_coverage)]):
                            new_guess_coverage[n] = cvg
                        guess_coverage = new_guess_coverage

                #If the point is of interest (in the d1/d2 vals) then use the
                #coverages from the initial guess map to try to find
                #the coverages.
                n = self.descriptor_decimal_precision
                if (np.round(point[0],n) in [np.round(d1V,n)
                        for d1V in d1Vals]
                        and np.round(point[1],n) in [np.round(d2V,n)
                            for d2V in d2Vals]):
                    i = [np.round(d1V,n)
                            for d1V in d1Vals].index(np.round(point[0],n))
                    j = [np.round(d2V,n)
                            for d2V in d2Vals].index(np.round(point[1],n))
                    try:
                        if not self.use_numbers_solver:
                            self._coverage = guess_coverage
                            self.get_point_output(point,guess_coverage)
                            point_coverages = self._coverage
                            self._coverage_map.append([point,point_coverages])
                        elif self.use_numbers_solver:
                            self.get_point_output(point,guess_coverage)
                            self._numbers_map.append([point, self._numbers])
                            self._coverage_map.append([point, self._coverage])
                        else:
                            raise ValueError('Unknown solver type')
                        isMapped[i,j] = int('1'+str(np.binary_repr(maxNum)), 2)
                        #Set this value above the max number
                        self.log('initial_success')
                    except ValueError:
                        self.log('initial_fail')
                else:
                    self._descriptors = point
                    self.log('initial_invalid')

        def check_by_minresid(possibilities, this_pt, bisect=False):
            """
                Helper function to iterate through "possibilities" and try them in
                order of minimum residual. Returns a list of "new possibilities"
                based on the best residual from each point.
            """
            new_possibilities = []
            tried = []
            for i_poss,poss in enumerate(possibilities): #sort by residual to
                # In the case of the default solver, c is a coverage
                # but in the case of the numbers solver, c is a number
                r, sol_pt, c = poss

                #use best guesses first
                self.log('minresid_status',
                        priority=1,
                        n_iter=i_poss,
                        old_pt=self.print_point(
                            sol_pt,self.descriptor_decimal_precision))

                if sol_pt not in tried:
                    try:
                        if bisect == False or this_pt == sol_pt: 
                            #don't bisect if the point is itself
                            # Calculate the coverage
                            self.get_point_output(this_pt, c)

                            point_coverages = self._coverage
                            if self.use_numbers_solver:
                                # Store the numbers for later use
                                point_numbers = self._numbers

                            if point_coverages:
                                self._coverage_map.append([this_pt,point_coverages])
                                if self.use_numbers_solver:
                                    self._numbers_map.append([this_pt,point_numbers])
                                self.log('minresid_success',n_iter=i_poss,
                                      old_pt=self.print_point(
                                      sol_pt,self.descriptor_decimal_precision))

                        else:
                            # In the case of the numbers solver we will bisect the value of x
                            # to generate a new initial guess.
                            point_coverages = self.bisect_descriptor_line(this_pt, sol_pt, c)

                            if point_coverages:
                                if self.use_numbers_solver:
                                    point_numbers = point_coverages
                                    point_coverages = self.solver.change_x_to_theta(point_coverages)
                                    # esites = len(self.site_names) - 1
                                    # point_coverages = point_coverages[:-esites]
                                    self._numbers_map.append([this_pt, point_numbers])

                                self._coverage_map.append([this_pt, point_coverages])

                            self.log('minresid_success', n_iter=i_poss, old_pt=sol_pt)

                        return None

                    except ValueError as strerror:
                        # Any ValueError during the coverage finding loop
                        # is processed here
                        strerror = str(strerror)
                        resid = strerror.split('resid=')[-1]
                        resid = resid.split(')')[0]
                        try:
                            resid_str = float(resid)
                        except ValueError:
                            resid_str = resid

                        self.log('minresid_fail',n_iter=i_poss,
                                old_pt=self.print_point(
                                    sol_pt,self.descriptor_decimal_precision),
                                resid=resid_str,
                                old_resid=float(r))

                        try:
                            resid = float(resid)
                            if this_pt != sol_pt:
                                new_possibilities.append([resid,sol_pt,c]) #make
                                #a new list of possibilities (used for
                                #bisections thus the boltzmann guess is
                                #omitted so that it is not tried twice)
                        except ValueError:
                            pass

                if sol_pt != this_pt: #allow multiple initial guesses
                    tried.append(sol_pt)

            return new_possibilities

        #Function to iterate through all points and use current
        #information to make the best guess
        def minresid_iteration(isMapped):
            """Go through all points and check the local solutions in
            order of minimum to maximum residual"""
            m,n = isMapped.shape
            for i in range(0,m):
                for j in range(0,n):
                    possibilities = []
                    this_pt = [d1Vals[i],d2Vals[j]]
                    self._descriptors = this_pt
                    if isMapped[i,j] < maxNum: #the point has not been found yet
                        #Get the list of possible guess coverages based
                        #on the search directions
                        checked_dirs = [int(bi)
                                for bi in np.binary_repr(isMapped[i,j])]
                        #Use binary representation to keep track of which
                        #directions have been checked
                        if len(checked_dirs) < len(self.search_directions):
                            #Add leading zeros if list is too small
                            checked_dirs = [0]*(len(self.search_directions) -
                                    len(checked_dirs))+checked_dirs
                        for k,direc in enumerate(self.search_directions):
                            dirx,diry = direc
                            solx = i + dirx
                            soly = j + diry
                            if (
                                    solx < m and
                                    solx >=0 and
                                    soly < n and
                                    soly>=0 and
                                    checked_dirs[k] == 0
                                    ): # point is in map and hasn't been checked
                                sol_pt = [d1Vals[solx],d2Vals[soly]]
                                if sol_pt != this_pt:
                                    sol_cvgs = self.retrieve_data(
                                            self._coverage_map,
                                            sol_pt,
                                            self.descriptor_decimal_precision)
                                    if self.use_numbers_solver:
                                        sol_numbers = self.retrieve_data(
                                                self._numbers_map,
                                                sol_pt,
                                                self.descriptor_decimal_precision)
                                else:
                                    # Note that in case of the numbers solver
                                    # the Bolzmann coverage is actually a 
                                    # "Boltzmann number" which include the slab
                                    boltz_cvgs = self.get_initial_coverage(this_pt)

                                    if self.max_initial_guesses is not None:
                                        max_initial_guesses = min(len(boltz_cvgs),self.max_initial_guesses)
                                        boltz_cvgs = boltz_cvgs[:max_initial_guesses]

                                    sol_cvgs = None
                                    for cvg in boltz_cvgs:
                                        self._coverage = cvg
                                        params = self.scaler.get_rxn_parameters(sol_pt)
                                        resid = self.solver.get_residual(cvg)
                                        possibilities.append(
                                                [resid,sol_pt,cvg])

                                    checked_dirs[k] = 1
                                if sol_cvgs:
                                    self._coverage = sol_cvgs
                                    if self.use_numbers_solver:
                                        self._numbers = sol_numbers
                                    params = self.scaler.get_rxn_parameters(
                                            sol_pt)
                                    resid = self.solver.get_residual(sol_cvgs)
                                    if self.use_numbers_solver:
                                        possibilities.append([resid,sol_pt,sol_numbers])
                                    else:
                                        possibilities.append([resid,sol_pt,sol_cvgs])
                                    checked_dirs[k] = 1
                            else: #point is not in map. make it "checked"
                                checked_dirs[k] = 1

                        #Now we have the "possibilities" for guess coverages
                        #and their residuals
                        possibilities = check_by_minresid(
                                possibilities,this_pt,bisect=False)
                        #Try without bisection since it is much faster
                        if possibilities and self.max_bisections>0:
                            #This implies the non-bisecting attempts failed.
                            if self.verbose > 0:
                               print('Unable to find solution at ' + \
                                       self.print_point(this_pt) + \
                                       ' from current information.'+ \
                                       ' Attempting bisection.')
                            possibilities = check_by_minresid(
                                   possibilities,
                                   this_pt,
                                   bisect=True)
                        elif (
                                possibilities is not None and
                                self.max_bisections>0
                                ):
                            if self.verbose > 0:
                                print('Unable to find solution at '+ \
                                       self.print_point(this_pt)+ \
                                       ' from current information. '+\
                                       'No nearby points for bisection.')

                        if possibilities is None:
                            #Set this value above the max number
                            isMapped[i,j] = int(
                                    '1'+str(np.binary_repr(maxNum)),2)

                        if isMapped[i,j] < maxNum:
                            #could not find solution. Add number to indicate
                            #how many directions have been checked. If new
                            #information becomes available later in this
                            #minresid iteration it will be utilized in the
                            #next minresid iteration
                            binstring = ''.join(
                                    [str(bi) for bi in checked_dirs])
                            isMapped[i,j] = int(binstring,2)

            return isMapped

        #Perform minresid iterations
        if not self._coverage_map:
            self._coverage_map = []
        norm_new = np.linalg.norm(isMapped)
        norm_old = -1
        minresiditer = 0
        while norm_new > norm_old: #Iterate thorough until no new information.
            m,n = isMapped.shape
            n_unmapped = 0 #Count how many points remain unmapped
            for i in range(0,m):
                for j in range(0,n):
                    if isMapped[i,j] <= maxNum:
                        n_unmapped+=1
            self.log('mapper_status',
                    n_unmapped=n_unmapped,
                    n_iter = minresiditer,
                    pt = 'mapper')
            minresiditer +=1
            norm_old = np.linalg.norm(isMapped)
            isMapped = minresid_iteration(isMapped)
            norm_new = np.linalg.norm(isMapped)

        if n_unmapped == 0:
            self.log('mapper_success',
                    n_iter = minresiditer,
                    pt = 'mapper', priority=1)
        else:
            self.log('mapper_fail',
                    n_unmapped=n_unmapped,
                    n_iter = minresiditer,
                    pt = 'mapper')

        # Remove duplicates and store the numbers map
        # for the numbers solver
        nodups = []
        if self.use_numbers_solver:
            nodups_numbers = []
        pts = []
        for pt,cvgs in self._coverage_map:
            if pt not in pts:
                pts.append(pt)
                nodups.append([pt,cvgs])
                if self.use_numbers_solver:
                    nodups_numbers.append(
                        [ pt,
                        self.retrieve_data(
                            self._numbers_map,
                            pt,
                            self.descriptor_decimal_precision ) 
                            ])
        self._coverage_map = nodups #remove duplicate points
        if self.use_numbers_solver:
            # Return both the coverage map and the
            # numbers map if the numbers solver is used
            self.numbers_map = nodups_numbers
            return self._coverage_map, self.numbers_map
        else:
            return self._coverage_map
